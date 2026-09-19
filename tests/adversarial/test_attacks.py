"""Adversarial scenarios: resource exhaustion, malformed input, repeated abuse, forgery attempts.

The invariant checked throughout: hostile input is rejected cheaply, with a stable error, and
leaves NO trace in persistent state.
"""
from __future__ import annotations

import dataclasses
import sqlite3
import time

import pytest

from mineai import consensus as C
from mineai.config import DEVNET, INT_MAX
from mineai.consensus import ValidationError
from tests.helpers import START, TEST, Acct, build, funded, make_chain, mai, mine, mine_n

TABLES = ("blocks", "accounts", "tx_index", "state_diffs", "mempool")


def row_counts(chain):
    return {t: chain.storage.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def test_profile_sanity_guards_reject_overflow_configs():
    with pytest.raises(ValueError):
        dataclasses.replace(DEVNET, max_supply=INT_MAX + 1)
    with pytest.raises(ValueError):
        dataclasses.replace(DEVNET, block_reward=DEVNET.max_supply + 1)
    with pytest.raises(ValueError):
        dataclasses.replace(DEVNET, min_fee=0)
    with pytest.raises(ValueError):
        dataclasses.replace(DEVNET, address_prefix="dmai")


def test_repeated_invalid_blocks_leave_no_trace(tmp_path):
    chain, alice = funded(tmp_path)
    before, size = row_counts(chain), (tmp_path / "chain.db").stat().st_size
    bad_builders = [
        lambda b: b.update(previous_hash="2" * 64), lambda b: b.update(difficulty=3),
        lambda b: b["transactions"][0].update(amount=10 ** 12), lambda b: b.update(timestamp=1),
    ]
    for i in range(200):
        block = build(chain, alice, bad_builders[i % 4])
        with pytest.raises(ValidationError):
            chain.submit_mined_block(block)
    assert row_counts(chain) == before
    assert (tmp_path / "chain.db").stat().st_size == size            # no disk growth from rejected data
    chain.verify_integrity()


def test_rejected_transactions_leave_no_trace(tmp_path):
    chain, alice = funded(tmp_path)
    before = row_counts(chain)
    bad = [alice.tx(Acct(), 1, nonce=9), alice.tx(Acct(), 1, fee="0.0009"), alice.tx(Acct(), 10 ** 6),
           alice.tx(Acct(), 1, timestamp=0)]
    for _ in range(50):
        for tx in bad:
            with pytest.raises(ValidationError):
                chain.submit_transaction(tx)
    assert row_counts(chain) == before


def test_block_with_absurd_transaction_count_is_rejected_fast(tmp_path):
    chain, alice = funded(tmp_path)
    block = build(chain, alice)
    block["transactions"] = [block["transactions"][0]] + [{}] * 1_000_000
    start = time.perf_counter()
    with pytest.raises(ValidationError) as exc:
        C.check_block_structure(block, chain.params)
    assert exc.value.code == "too_large" and time.perf_counter() - start < 0.5   # length is checked before any per-tx work


def test_huge_strings_are_rejected_without_heavy_work(tmp_path):
    chain, alice = funded(tmp_path)
    tx = alice.tx(Acct(), 1)
    start = time.perf_counter()
    for field in ("sender", "recipient", "public_key", "signature", "txid"):
        with pytest.raises(ValidationError):
            C.check_transaction({**tx, field: "A" * 5_000_000}, chain.params)
    assert time.perf_counter() - start < 2.0


@pytest.mark.parametrize("junk", [
    "\x00" * 44, "é" * 44, "😀" * 10, "\n" * 5, "A" * 43 + "\x00", "=" * 44, "%%%%", "‮" + "A" * 43,
    "\udc80abc",
])
def test_malformed_binary_looking_data_in_every_string_field(tmp_path, junk):
    chain, alice = funded(tmp_path)
    tx = alice.tx(Acct(), 1)
    for field in ("sender", "recipient", "public_key", "signature", "txid"):
        with pytest.raises(ValidationError):                 # a ValidationError, never UnicodeError/TypeError
            C.check_transaction({**tx, field: junk}, chain.params)


@pytest.mark.parametrize("value", [None, True, False, 1.5, "1", [], {}, (1,), b"1", float("nan"), float("inf"), 2 ** 64, -2 ** 64])
def test_every_integer_field_rejects_wrong_types_and_extremes(tmp_path, value):
    chain, alice = funded(tmp_path)
    tx = alice.tx(Acct(), 1)
    for field in ("amount", "fee", "nonce", "timestamp"):
        with pytest.raises(ValidationError):
            C.check_transaction({**tx, field: value}, chain.params)
    block = build(chain, alice)
    for field in ("height", "timestamp", "nonce", "difficulty"):
        with pytest.raises(ValidationError):
            C.check_block_structure({**block, field: value}, chain.params)


def test_cannot_forge_a_coinbase_as_a_regular_transaction(tmp_path):
    chain, alice = funded(tmp_path)
    forged = C.coinbase_tx(chain.params, 4, alice.address, mai(1000), 0)
    def cheat(block):
        block["transactions"].append(forged)
    with pytest.raises(ValidationError) as exc:
        chain.submit_mined_block(build(chain, alice, cheat))
    assert exc.value.code == "bad_fields"
    with pytest.raises(ValidationError):
        chain.submit_transaction(forged)


def test_deep_fork_attempt_is_rejected_without_side_effects(tmp_path):
    chain, alice = funded(tmp_path, blocks=6)
    fork_point = chain.storage.get_block_by_height(2)
    before = row_counts(chain)
    def fork(block):
        block.update(height=3, previous_hash=fork_point["hash"])
    with pytest.raises(ValidationError) as exc:
        chain.submit_mined_block(build(chain, alice, fork))
    assert exc.value.code == "stale"
    assert row_counts(chain) == before and chain.tip()["height"] == 6


def test_timestamp_drift_attack_is_capped_by_wall_clock(tmp_path):
    """A miner may push timestamps forward, but never beyond now + max_future, however it chains blocks."""
    chain = make_chain(tmp_path)
    miner = Acct()
    limit = chain.now() + TEST.max_future_seconds
    accepted = 0
    for _ in range(12):
        ts = min(limit, chain.tip()["timestamp"] + 3600)
        try:
            chain.submit_mined_block(build(chain, miner, lambda b, ts=ts: b.update(timestamp=ts)))
            accepted += 1
        except ValidationError:
            pass
    assert all(chain.storage.get_block_by_height(h)["timestamp"] <= limit for h in range(1, chain.tip()["height"] + 1))
    assert accepted >= 1
    # and a block one second beyond the limit is refused
    with pytest.raises(ValidationError) as exc:
        chain.submit_mined_block(build(chain, miner, lambda b: b.update(timestamp=limit + 1)))
    assert exc.value.code == "bad_timestamp"


def test_timestamp_backwards_drift_is_refused(tmp_path):
    """Rule: timestamp must exceed the MEDIAN of the last 11 blocks (not merely the previous block)."""
    chain = make_chain(tmp_path)
    miner = Acct()
    mine_n(chain, miner, 12)
    mtp = C.median_time_past(chain.storage.recent_timestamps(TEST.mtp_window))
    assert mtp < chain.tip()["timestamp"]
    for delta in (0, 1, 100, 10 ** 6):                            # at or below the median: always refused
        with pytest.raises(ValidationError) as exc:
            chain.submit_mined_block(build(chain, miner, lambda b, d=delta: b.update(timestamp=mtp - d)))
        assert exc.value.code == "bad_timestamp"
    tip_before = chain.tip()["timestamp"]
    chain.submit_mined_block(build(chain, miner, lambda b: b.update(timestamp=mtp + 1)))   # just above: allowed
    assert chain.tip()["timestamp"] == mtp + 1 < tip_before        # documented behaviour: may be older than the tip


def test_mempool_memory_is_bounded_under_flooding(tmp_path):
    limited = dataclasses.replace(TEST, mempool_max_txs=20, mempool_max_per_sender=1000)
    chain = make_chain(tmp_path, limited)
    alice, sink = Acct(limited), Acct(limited)
    mine_n(chain, alice, 6)
    accepted = rejected = 0
    for nonce in range(1, 60):
        try:
            chain.submit_transaction(alice.tx(sink, "0.5", nonce=nonce, timestamp=START + nonce))
            accepted += 1
        except ValidationError as exc:
            assert exc.code == "mempool_full"
            rejected += 1
    assert accepted == 20 and rejected == 39
    assert chain.storage.mempool_count() == 20


def test_dust_spam_is_limited_by_minimum_fee(tmp_path):
    chain, alice = funded(tmp_path)
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(alice.tx(Acct(), "0.000001", fee="0.0009"))
    assert exc.value.code == "out_of_range"


def test_replay_attack_across_time_and_chains(tmp_path):
    chain, alice = funded(tmp_path)
    bob = Acct()
    tx = alice.tx(bob, 2)
    chain.submit_transaction(tx)
    mine(chain, alice)
    for _ in range(3):                                            # resubmitting a confirmed tx never works
        with pytest.raises(ValidationError):
            chain.submit_transaction(tx)
    assert chain.account(bob.address)["balance"] == mai(2)


def test_sender_cannot_spend_recipient_funds_it_does_not_hold(tmp_path):
    chain, alice = funded(tmp_path)
    bob, carol = Acct(), Acct()
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(bob.tx(carol, 1))                # bob has nothing
    assert exc.value.code == "insufficient_funds"


def test_supply_invariant_holds_after_a_long_random_workload(tmp_path):
    import random
    rng = random.Random(1234)
    chain = make_chain(tmp_path)
    people = [Acct() for _ in range(5)]
    mine_n(chain, people[0], 4)
    for _ in range(40):
        sender, receiver = rng.sample(people, 2)
        info = chain.account(sender.address)
        amount = rng.choice(["0.5", "1", "3", "0.000001"])
        try:
            chain.submit_transaction(sender.tx(receiver, amount, fee=rng.choice(["0.001", "0.01"]),
                                               nonce=info["next_nonce"], timestamp=chain.now()))
        except ValidationError:
            pass
        if rng.random() < 0.6:
            mine(chain, rng.choice(people))
            chain.clock.advance(rng.randint(1, 120))
        assert chain.storage.total_balances() == chain.minted_supply()
    chain.verify_integrity()


def test_sqlite_injection_strings_are_inert(tmp_path):
    chain, alice = funded(tmp_path)
    for evil in ("' OR 1=1 --", "'; DROP TABLE accounts; --", "\" OR \"\"=\""):
        with pytest.raises(ValidationError):
            chain.account(evil)
        with pytest.raises(ValidationError):
            chain.submit_transaction({**alice.tx(Acct(), 1), "recipient": evil})
    assert chain.storage.get_block_by_hash("' OR 1=1 --") is None
    assert row_counts(chain)["accounts"] > 0
