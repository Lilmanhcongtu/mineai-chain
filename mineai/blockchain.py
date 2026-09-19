"""Blockchain state machine: applies consensus rules (consensus.py) to persistent state (storage.py).

Account model: state = {address: (balance, nonce)}. Balances are integer atomic units.
Nothing here depends on a web framework.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from . import consensus as C
from .config import NetworkParams, get_params
from .consensus import ValidationError
from .crypto import validate_address
from .log import event
from .storage import Storage, StorageError

log = logging.getLogger("mineai.chain")


class ChainError(RuntimeError):
    """The local database is inconsistent with the selected network."""


class _Ctx:
    """Scratch state used while applying a sequence of transactions at a given height."""

    def __init__(self, chain: "Blockchain", height: int):
        self.chain, self.height = chain, height
        p = chain.params
        self.immature = chain.storage.coinbase_amounts(height - p.coinbase_maturity + 1, height - 1)
        self.overlay: dict[str, list[int]] = {}
        self.undo: dict[str, tuple[int, int]] = {}
        self.fees = 0

    def acct(self, address: str) -> list[int]:
        if address not in self.overlay:
            balance, nonce = self.chain.storage.get_account(address)
            self.undo[address] = (balance, nonce)
            self.overlay[address] = [balance, nonce]
        return self.overlay[address]

    def apply(self, tx: dict) -> None:
        """Validate `tx` against current scratch state and apply it. Raises before mutating."""
        sender = self.acct(tx["sender"])
        if tx["nonce"] != sender[1] + 1:
            raise ValidationError(f"invalid nonce: expected {sender[1] + 1}", "bad_nonce")
        spendable = sender[0] - self.immature.get(tx["sender"], 0)
        cost = tx["amount"] + tx["fee"]
        if cost > spendable:
            raise ValidationError("insufficient spendable balance", "insufficient_funds")
        recipient = self.acct(tx["recipient"])
        sender[0] -= cost
        sender[1] = tx["nonce"]
        recipient[0] += tx["amount"]
        self.fees += tx["fee"]

    def changes(self) -> dict[str, tuple[int, int]]:
        return {a: (v[0], v[1]) for a, v in self.overlay.items()}


class Blockchain:
    def __init__(self, db_path: Path, params: NetworkParams | None = None,
                 clock: Callable[[], float] = time.time):
        self.params = params or get_params()
        self.clock = clock
        self.lock = threading.RLock()
        self.storage = Storage(db_path)
        try:
            self._open()
        except Exception:
            self.storage.close()
            raise

    def close(self) -> None:
        self.storage.close()

    # ------------------------------------------------------------------ startup checks
    def _open(self) -> None:
        p = self.params
        genesis = C.genesis_block(p)
        if p.genesis_hash and genesis["hash"] != p.genesis_hash:
            raise ChainError("computed genesis hash does not match the pinned value for "
                             f"{p.name}: the software and the network profile disagree")
        if self.storage.block_count() == 0:
            with self.storage.atomic():
                self.storage.insert_genesis(genesis)
                self.storage.meta_set("network_id", p.network_id)
                self.storage.meta_set("genesis_hash", genesis["hash"])
            event(log, logging.INFO, "chain_initialized", network=p.name, genesis=genesis["hash"])
            return
        stored_net = self.storage.meta_get("network_id")
        if stored_net != p.network_id:
            raise ChainError(f"database belongs to network {stored_net!r}, not {p.network_id!r}")
        stored = self.storage.get_block_by_height(0)
        if stored is None or stored["hash"] != genesis["hash"] or \
                self.storage.meta_get("genesis_hash") != genesis["hash"]:
            raise ChainError("stored genesis block does not match this network")
        tip = self.storage.latest_block()
        if C.block_hash(p, tip) != tip["hash"]:
            raise ChainError("tip block hash does not verify: database corrupted")
        if self.storage.total_balances() != self.minted_supply():
            raise ChainError("sum of balances differs from minted supply: database corrupted")
        event(log, logging.INFO, "chain_opened", network=p.name, height=tip["height"], tip=tip["hash"])

    def verify_integrity(self) -> None:
        """Full offline re-verification of the stored chain and replay of balances. Slow: O(chain)."""
        try:
            self._verify_integrity()
        except ValidationError as exc:
            raise ChainError(f"stored chain violates consensus rules: {exc}") from exc

    def _verify_integrity(self) -> None:
        p, prev, minted = self.params, None, 0
        balances: dict[str, int] = {}
        nonces: dict[str, int] = {}
        for block in self.storage.iter_blocks_ascending():
            if block["height"] == 0:
                if block["hash"] != C.genesis_block(p)["hash"]:
                    raise ChainError("genesis mismatch")
                prev = block
                continue
            C.check_block_structure({k: block[k] for k in C.BLOCK_FIELDS}, p)
            if block["previous_hash"] != prev["hash"] or block["height"] != prev["height"] + 1:
                raise ChainError(f"broken link at height {block['height']}")
            subsidy = C.subsidy_for(minted, p)
            fees = 0
            for tx in block["transactions"][1:]:
                if tx["nonce"] != nonces.get(tx["sender"], 0) + 1:
                    raise ChainError(f"bad nonce sequence at height {block['height']}")
                balances[tx["sender"]] = balances.get(tx["sender"], 0) - tx["amount"] - tx["fee"]
                if balances[tx["sender"]] < 0:
                    raise ChainError(f"negative balance at height {block['height']}")
                nonces[tx["sender"]] = tx["nonce"]
                balances[tx["recipient"]] = balances.get(tx["recipient"], 0) + tx["amount"]
                fees += tx["fee"]
            C.check_coinbase(block["transactions"][0], p, block["height"], subsidy, fees)
            cb = block["transactions"][0]
            balances[cb["recipient"]] = balances.get(cb["recipient"], 0) + cb["amount"]
            minted += subsidy
            prev = block
        if minted != self.minted_supply():
            raise ChainError("minted supply mismatch")
        for address, balance in balances.items():
            if self.storage.get_account(address) != (balance, nonces.get(address, 0)):
                raise ChainError(f"account state mismatch for {address}")

    # ------------------------------------------------------------------ read helpers
    def now(self) -> int:
        return int(self.clock())

    def tip(self) -> dict:
        return self.storage.latest_block()

    def minted_supply(self) -> int:
        return int(self.storage.meta_get("minted_supply") or 0)

    def next_subsidy(self) -> int:
        return C.subsidy_for(self.minted_supply(), self.params)

    def account(self, address: str) -> dict:
        if not validate_address(address, self.params.address_prefix):
            raise ValidationError("invalid MAI address", "bad_address")
        with self.lock:
            balance, nonce = self.storage.get_account(address)
            height = self.tip()["height"] + 1
            immature = self.storage.coinbase_amounts(
                height - self.params.coinbase_maturity + 1, height - 1).get(address, 0)
            pending = self.storage.mempool_for_sender(address)
            pending_out = sum(t["amount"] + t["fee"] for t in pending)
            return {
                "address": address,
                "balance": balance,
                "immature_balance": immature,
                "available_balance": balance - immature - pending_out,
                "confirmed_nonce": nonce,
                "next_nonce": nonce + len(pending) + 1,
                "pending_outgoing": pending_out,
            }

    def find_transaction(self, txid: str) -> dict | None:
        loc = self.storage.tx_location(txid)
        if loc:
            height, index = loc
            block = self.storage.get_block_by_height(height)
            return {"status": "confirmed", "height": height, "index": index,
                    "confirmations": self.tip()["height"] - height + 1,
                    "block_hash": block["hash"], "transaction": block["transactions"][index]}
        tx = self.storage.mempool_get(txid)
        return {"status": "mempool", "confirmations": 0, "transaction": tx} if tx else None

    # ------------------------------------------------------------------ mempool
    def submit_transaction(self, tx: dict, now: int | None = None) -> dict:
        p = self.params
        now = self.now() if now is None else now
        try:
            C.check_transaction(tx, p)
            with self.lock:
                self._admit(tx, now)
        except ValidationError as exc:
            event(log, logging.INFO, "tx_rejected", code=exc.code,
                  txid=tx.get("txid") if isinstance(tx, dict) else None)
            raise
        event(log, logging.INFO, "tx_accepted", txid=tx["txid"])
        return tx

    def _admit(self, tx: dict, now: int) -> None:
        p, st = self.params, self.storage
        if abs(tx["timestamp"] - now) > p.mempool_timestamp_window:
            raise ValidationError("transaction timestamp is outside the acceptable window", "bad_timestamp")
        if st.tx_location(tx["txid"]) or st.mempool_contains(tx["txid"]):
            raise ValidationError("duplicate transaction", "duplicate_tx")
        if st.mempool_count() >= p.mempool_max_txs:
            raise ValidationError("mempool is full", "mempool_full")
        info = self.account(tx["sender"])
        if info["next_nonce"] - info["confirmed_nonce"] - 1 >= p.mempool_max_per_sender:
            raise ValidationError("too many pending transactions for this sender", "mempool_sender_limit")
        if tx["nonce"] != info["next_nonce"]:
            raise ValidationError(
                f"invalid nonce: expected {info['next_nonce']} (replacement of pending transactions "
                "is not supported)", "bad_nonce")
        if tx["amount"] + tx["fee"] > info["available_balance"]:
            raise ValidationError("insufficient available balance", "insufficient_funds")
        try:
            st.mempool_add(tx)
        except StorageError as exc:
            raise ValidationError(str(exc), "duplicate_tx") from exc

    def _prune_mempool(self) -> None:
        """Drop mempool transactions that can no longer be mined. Call inside storage.atomic()."""
        st, height = self.storage, self.tip()["height"] + 1
        immature = st.coinbase_amounts(height - self.params.coinbase_maturity + 1, height - 1)
        drop: list[str] = []
        for sender in st.mempool_senders():
            balance, nonce = st.get_account(sender)
            spendable = balance - immature.get(sender, 0)
            broken = False
            for tx in st.mempool_for_sender(sender):
                cost = tx["amount"] + tx["fee"]
                if broken or tx["nonce"] != nonce + 1 or cost > spendable:
                    drop.append(tx["txid"])
                    broken = broken or tx["nonce"] > nonce + 1 or cost > spendable
                    continue
                nonce += 1
                spendable -= cost
        if drop:
            st.mempool_remove(drop)
            event(log, logging.INFO, "mempool_pruned", removed=len(drop))

    # ------------------------------------------------------------------ mining
    def mining_template(self, miner_address: str, now: int | None = None) -> dict:
        p = self.params
        if not validate_address(miner_address, p.address_prefix):
            raise ValidationError("invalid miner address", "bad_address")
        now = self.now() if now is None else now
        with self.lock:
            tip = self.tip()
            height = tip["height"] + 1
            mtp = C.median_time_past(self.storage.recent_timestamps(p.mtp_window))
            ctx = _Ctx(self, height)
            selected: list[dict] = []
            remaining = self.storage.mempool_list()
            progress = True
            while progress and remaining and len(selected) < p.max_regular_txs_per_block:
                progress, deferred = False, []
                for tx in remaining:
                    if len(selected) >= p.max_regular_txs_per_block:
                        break
                    try:
                        ctx.apply(tx)
                    except ValidationError:
                        deferred.append(tx)      # may become valid once an earlier nonce is selected
                    else:
                        selected.append(tx)
                        progress = True
                remaining = deferred
            subsidy = self.next_subsidy()
            cb = C.coinbase_tx(p, height, miner_address, subsidy, ctx.fees)
            txs = [cb] + selected
            return {
                "network_id": p.network_id,
                "height": height,
                "previous_hash": tip["hash"],
                "merkle_root": C.merkle_root([t["txid"] for t in txs]),
                "timestamp": max(now, mtp + 1),
                "difficulty": p.difficulty,
                "nonce": 0,
                "transactions": txs,
            }

    def submit_mined_block(self, block: dict, now: int | None = None) -> dict:
        now = self.now() if now is None else now
        try:
            with self.lock:
                accepted = self._accept_block(block, now)
        except ValidationError as exc:
            event(log, logging.INFO, "block_rejected", code=exc.code,
                  height=block.get("height") if isinstance(block, dict) else None)
            raise
        event(log, logging.INFO, "block_accepted", height=accepted["height"], hash=accepted["hash"],
              txs=len(accepted["transactions"]) - 1)
        return accepted

    def _accept_block(self, block: dict, now: int) -> dict:
        p, st = self.params, self.storage
        C.check_block_structure(block, p)                       # stateless rules
        tip = self.tip()
        if block["height"] != tip["height"] + 1 or block["previous_hash"] != tip["hash"]:
            raise ValidationError("stale block: does not extend the current tip", "stale")
        mtp = C.median_time_past(st.recent_timestamps(p.mtp_window))
        C.check_block_time(block["timestamp"], mtp, now, p)
        txs = block["transactions"]
        ctx = _Ctx(self, block["height"])
        for tx in txs[1:]:
            if st.tx_location(tx["txid"]):
                raise ValidationError("transaction already confirmed", "duplicate_tx")
            ctx.apply(tx)
        minted = self.minted_supply()
        subsidy = C.subsidy_for(minted, p)
        cb = C.check_coinbase(txs[0], p, block["height"], subsidy, ctx.fees)
        ctx.acct(cb["recipient"])[0] += cb["amount"]
        stored = dict(block)
        with st.atomic():                                        # block + state + mempool: all or nothing
            st.apply_block(stored, cb["recipient"], subsidy, ctx.fees, ctx.changes(), ctx.undo, minted + subsidy)
            st.mempool_remove([t["txid"] for t in txs[1:]])
            self._prune_mempool()
        stored.update(miner=cb["recipient"], subsidy=subsidy, fees=ctx.fees)
        return stored
