"""Pure consensus rules: no I/O, no clocks (time is passed in), no database, no web framework.

Everything here must match PROTOCOL.md exactly.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import struct

from .config import INT_MAX, NetworkParams
from .crypto import (
    HEX64, b64d_strict, coinbase_txid, compute_txid, lp, u64, validate_address,
    verify_transaction_signature,
)

BLOCK_DOMAIN = b"MineAI/block/v1\x00"
LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"
ZERO_HASH = "0" * 64

TX_FIELDS = frozenset({"txid", "sender", "recipient", "amount", "fee", "nonce",
                       "timestamp", "public_key", "signature"})
COINBASE_FIELDS = frozenset({"type", "txid", "height", "recipient", "subsidy", "fees", "amount"})
BLOCK_FIELDS = frozenset({"height", "previous_hash", "merkle_root", "timestamp", "difficulty",
                          "nonce", "hash", "transactions"})


class ValidationError(ValueError):
    """A consensus or policy rule was violated. `.code` is a stable machine-readable reason."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def _int(value, name: str, lo: int = 0, hi: int = INT_MAX) -> int:
    if type(value) is not int:          # rejects bool, float, str
        raise ValidationError(f"{name} must be an integer", "bad_type")
    if not lo <= value <= hi:
        raise ValidationError(f"{name} out of range", "out_of_range")
    return value


def _hash(value, name: str) -> str:
    if not isinstance(value, str) or not HEX64.match(value):
        raise ValidationError(f"{name} must be 64 lower-case hex characters", "bad_hash")
    return value


# ------------------------------------------------------------------ sizes
def tx_size(tx: dict) -> int:
    return len(json.dumps(tx, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii"))


# ------------------------------------------------------------------ transactions
def check_transaction(tx: object, params: NetworkParams) -> dict:
    """Stateless validation of a regular transaction (structure, bounds, signature, txid)."""
    if not isinstance(tx, dict) or set(tx) != TX_FIELDS:
        raise ValidationError("transaction must contain exactly the required fields", "bad_fields")
    for name in ("sender", "recipient", "public_key", "signature", "txid"):
        if not isinstance(tx[name], str):
            raise ValidationError(f"{name} must be a string", "bad_type")
    _int(tx["amount"], "amount", 1, params.max_supply)
    _int(tx["fee"], "fee", params.min_fee, params.max_supply)
    _int(tx["nonce"], "nonce", 1)
    _int(tx["timestamp"], "timestamp")
    _hash(tx["txid"], "txid")
    if not validate_address(tx["sender"], params.address_prefix):
        raise ValidationError("invalid sender address", "bad_address")
    if not validate_address(tx["recipient"], params.address_prefix):
        raise ValidationError("invalid recipient address", "bad_address")
    if tx["sender"] == tx["recipient"]:
        raise ValidationError("sender and recipient must differ", "self_transfer")
    if tx_size(tx) > params.max_tx_bytes:
        raise ValidationError("transaction too large", "too_large")
    if not verify_transaction_signature(tx, params.network_id, params.address_prefix):
        raise ValidationError("invalid signature or txid", "bad_signature")
    return tx


def coinbase_tx(params: NetworkParams, height: int, recipient: str, subsidy: int, fees: int) -> dict:
    return {
        "type": "coinbase",
        "txid": coinbase_txid(params.network_id, height, recipient, subsidy, fees),
        "height": height, "recipient": recipient,
        "subsidy": subsidy, "fees": fees, "amount": subsidy + fees,
    }


def check_coinbase(cb: object, params: NetworkParams, height: int, subsidy: int, fees: int) -> dict:
    if not isinstance(cb, dict) or set(cb) != COINBASE_FIELDS or cb.get("type") != "coinbase":
        raise ValidationError("first transaction must be a well-formed coinbase", "bad_coinbase")
    if not validate_address(cb["recipient"], params.address_prefix):
        raise ValidationError("invalid miner address", "bad_address")
    expected = coinbase_tx(params, height, cb["recipient"], subsidy, fees)
    for key in ("height", "subsidy", "fees", "amount"):
        _int(cb[key], key)
    if cb != expected:
        raise ValidationError("incorrect coinbase (height, subsidy, fees or txid)", "bad_coinbase")
    return cb


# ------------------------------------------------------------------ economics
def subsidy_for(minted_supply: int, params: NetworkParams) -> int:
    """Block subsidy: the fixed reward, clipped so total minted supply never exceeds the cap."""
    return max(0, min(params.block_reward, params.max_supply - minted_supply))


# ------------------------------------------------------------------ hashing
def merkle_root(txids: list[str]) -> str:
    """Binary Merkle tree over txids. Leaf = SHA256(0x00||txid_bytes), node = SHA256(0x01||l||r).
    An odd node is promoted unchanged (no duplication). Empty list -> 64 zeros."""
    if not txids:
        return ZERO_HASH
    level = [hashlib.sha256(LEAF_PREFIX + bytes.fromhex(t)).digest() for t in txids]
    while len(level) > 1:
        nxt = [hashlib.sha256(NODE_PREFIX + level[i] + level[i + 1]).digest()
               for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0].hex()


def header_prefix(params: NetworkParams, height: int, previous_hash: str, merkle: str,
                  timestamp: int, difficulty: int) -> bytes:
    """Everything in the header except the nonce (miners reuse this)."""
    return (BLOCK_DOMAIN + lp(params.network_id) + u64(height)
            + bytes.fromhex(previous_hash) + bytes.fromhex(merkle)
            + u64(timestamp) + u64(difficulty))


def hash_header(prefix: bytes, nonce: int) -> str:
    return hashlib.sha256(prefix + u64(nonce)).hexdigest()


def block_hash(params: NetworkParams, block: dict) -> str:
    prefix = header_prefix(params, block["height"], block["previous_hash"], block["merkle_root"],
                           block["timestamp"], block["difficulty"])
    return hash_header(prefix, block["nonce"])


MAX_DIFFICULTY = 2 ** 62


def target_for(difficulty: int) -> int:
    """Largest hash value (as a 256-bit big-endian integer) that satisfies `difficulty`."""
    return (2 ** 256 - 1) // difficulty


def meets_target(hash_hex: str, difficulty: int) -> bool:
    return int(hash_hex, 16) <= target_for(difficulty)


def block_work(difficulty: int) -> int:
    """Work of one block = its difficulty (expected number of hashes). Genesis has work 0."""
    return difficulty


TS_FILTER = 5          # timestamps are median-filtered over this many blocks before use


def _filtered(stamps: list[int], i: int) -> int:
    """Median of the (up to) TS_FILTER timestamps ending at index i; the upper median for an even count."""
    segment = stamps[max(0, i - TS_FILTER + 1): i + 1]
    return sorted(segment)[len(segment) // 2]


def next_difficulty(params: NetworkParams, history: list[tuple[int, int]]) -> int:
    """Difficulty required of the child of the last block in `history` (PROTOCOL.md 5.10).

    `history`: (timestamp, difficulty) of the parent and up to lwma_window + TS_FILTER - 1 of its ancestors,
    OLDEST first (it starts at genesis when the chain is that short). Integer arithmetic only.
    The first TS_FILTER blocks of a chain use the initial difficulty (the filter needs full segments)."""
    count = len(history)
    window = min(params.lwma_window, count - TS_FILTER)
    if window < 1:
        return params.difficulty
    stamps = [t for t, _ in history]
    target = params.target_spacing
    weighted, total = 0, 0
    prev = _filtered(stamps, count - window - 1)
    for j in range(1, window + 1):
        i = count - window - 1 + j
        stamp = _filtered(stamps, i)
        effective = stamp if stamp > prev else prev + 1               # strictly increasing timestamps
        solve = min(6 * target, effective - prev)                     # cap a single solve time at 6 targets
        prev = effective
        weighted += j * solve
        total += history[i][1]
    raw = (total * (window + 1) * target) // (2 * weighted)
    parent = history[-1][1]
    nxt = min(raw, 2 * parent)                                        # at most 2x up per block
    nxt = max(nxt, parent // 2)                                       # at most 2x down per block
    return max(1, min(MAX_DIFFICULTY, max(params.min_difficulty, nxt)))


def genesis_block(params: NetworkParams) -> dict:
    block = {"height": 0, "previous_hash": ZERO_HASH, "merkle_root": ZERO_HASH,
             "timestamp": params.genesis_timestamp, "difficulty": 0, "nonce": 0, "transactions": []}
    block["hash"] = block_hash(params, block)
    return block


# ------------------------------------------------------------------ blocks
def check_block_structure(block: object, params: NetworkParams, expected_difficulty: int | None = None) -> dict:
    """Stateless block checks: fields, types, sizes, transaction well-formedness, merkle root,
    hash and proof of work. State-dependent rules live in Blockchain."""
    if not isinstance(block, dict) or set(block) != BLOCK_FIELDS:
        raise ValidationError("block must contain exactly the required fields", "bad_fields")
    height = _int(block["height"], "height", 1)
    _hash(block["previous_hash"], "previous_hash")
    _hash(block["merkle_root"], "merkle_root")
    _hash(block["hash"], "hash")
    _int(block["timestamp"], "timestamp")
    _int(block["nonce"], "nonce")
    difficulty = _int(block["difficulty"], "difficulty", 1, MAX_DIFFICULTY)
    txs = block["transactions"]
    if not isinstance(txs, list) or not txs:
        raise ValidationError("block must contain a coinbase transaction", "bad_coinbase")
    if len(txs) - 1 > params.max_regular_txs_per_block:
        raise ValidationError("too many transactions", "too_large")
    for tx in txs[1:]:
        check_transaction(tx, params)
    ids = [t["txid"] for t in txs[1:]]
    if len(set(ids)) != len(ids):
        raise ValidationError("duplicate transaction in block", "duplicate_tx")
    if sum(tx_size(t) for t in txs) > params.max_block_bytes:
        raise ValidationError("block too large", "too_large")
    if not isinstance(txs[0], dict) or not isinstance(txs[0].get("txid"), str):
        raise ValidationError("first transaction must be a well-formed coinbase", "bad_coinbase")
    _hash(txs[0]["txid"], "coinbase txid")
    if merkle_root([txs[0]["txid"]] + ids) != block["merkle_root"]:
        raise ValidationError("merkle root mismatch", "bad_merkle")
    if difficulty != (params.difficulty if expected_difficulty is None else expected_difficulty):
        raise ValidationError("incorrect difficulty for this height", "bad_difficulty")
    if block_hash(params, block) != block["hash"]:
        raise ValidationError("incorrect block hash", "bad_hash")
    if not meets_target(block["hash"], difficulty):
        raise ValidationError("proof of work does not meet target", "bad_pow")
    return block


def median_time_past(timestamps: list[int]) -> int:
    return int(statistics.median(timestamps))


def check_block_time(timestamp: int, mtp: int, now: int, params: NetworkParams) -> None:
    if timestamp <= mtp:
        raise ValidationError("block timestamp must be greater than the median of recent blocks",
                              "bad_timestamp")
    if timestamp > now + params.max_future_seconds:
        raise ValidationError("block timestamp is too far in the future", "bad_timestamp")
