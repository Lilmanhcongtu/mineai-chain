"""Shared test helpers: deterministic clock, funded accounts, block builder/solver."""
from __future__ import annotations

import dataclasses

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mineai import consensus as C
from mineai.blockchain import Blockchain
from mineai.config import ATOMIC_UNITS, DEVNET
from mineai.crypto import address_from_public_key, public_key_bytes, sign_transaction

# Fast profile: difficulty 1 (16 hashes on average) and a short coinbase maturity.
TEST = dataclasses.replace(DEVNET, difficulty=1, coinbase_maturity=2)
START = 1_800_000_000   # after the devnet genesis timestamp


def mai(x) -> int:
    from mineai.util import mai_to_atomic
    return mai_to_atomic(str(x))


class Clock:
    def __init__(self, t: int = START):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: int = 60) -> None:
        self.t += seconds


class Acct:
    def __init__(self, params=TEST):
        self.params = params
        self.key = Ed25519PrivateKey.generate()
        self.address = address_from_public_key(public_key_bytes(self.key), params.address_prefix)

    def tx(self, to: "Acct | str", amount=None, fee="0.01", nonce=1, timestamp=START, *,
           atomic: int | None = None) -> dict:
        """`amount`/`fee` are MAI (str or int). Pass `atomic=` for a raw atomic-unit amount."""
        recipient = to.address if isinstance(to, Acct) else to
        return sign_transaction(self.key, {
            "recipient": recipient, "amount": atomic if atomic is not None else mai(amount),
            "fee": mai(fee), "nonce": nonce, "timestamp": timestamp,
        }, self.params.network_id, self.params.address_prefix)


def make_chain(tmp_path, params=TEST, clock: Clock | None = None, name="chain.db") -> Blockchain:
    return Blockchain(tmp_path / name, params, clock=clock or Clock())


def solve(params, block: dict, difficulty: int | None = None) -> dict:
    """Recompute merkle root, then find a nonce meeting the (given or block's) difficulty."""
    block["merkle_root"] = C.merkle_root([t["txid"] for t in block["transactions"]])
    if difficulty is not None:
        block["difficulty"] = difficulty
    prefix = C.header_prefix(params, block["height"], block["previous_hash"], block["merkle_root"],
                             block["timestamp"], block["difficulty"])
    nonce = 0
    while not C.meets_target(C.hash_header(prefix, nonce), block["difficulty"]):
        nonce += 1
    block["nonce"] = nonce
    block["hash"] = C.hash_header(prefix, nonce)
    return block


def build(chain: Blockchain, miner: "Acct | str", mutate=None, now: int | None = None) -> dict:
    """Template -> optional mutation -> solved block (not yet submitted)."""
    address = miner.address if isinstance(miner, Acct) else miner
    template = chain.mining_template(address, now=now)
    block = {k: template[k] for k in ("height", "previous_hash", "merkle_root", "timestamp",
                                      "difficulty", "nonce", "transactions")}
    if mutate:
        mutate(block)
    return solve(chain.params, block)


def mine(chain: Blockchain, miner: "Acct | str", mutate=None, now: int | None = None) -> dict:
    return chain.submit_mined_block(build(chain, miner, mutate, now), now=now)


def mine_n(chain: Blockchain, miner, n: int) -> None:
    for _ in range(n):
        mine(chain, miner)
        chain.clock.advance(60)


def funded(tmp_path, blocks: int = 3, params=TEST):
    """A chain plus an account that mined `blocks` blocks (enough to have spendable coins)."""
    chain = make_chain(tmp_path, params)
    alice = Acct(params)
    mine_n(chain, alice, blocks)
    return chain, alice
