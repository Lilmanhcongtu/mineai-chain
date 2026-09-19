"""Consensus rule tests: every rule is exercised with a violating input that must be rejected,
and the chain state must be provably unchanged afterwards."""
from __future__ import annotations

import dataclasses

import pytest

from mineai import consensus as C
from mineai.consensus import ValidationError
from tests.helpers import START, TEST, Acct, build, funded, make_chain, mai, mine, mine_n, solve


def snapshot(chain):
    tip = chain.tip()
    return (tip["height"], tip["hash"], chain.minted_supply(), chain.storage.total_balances(),
            chain.storage.mempool_count())


def reject(chain, block, code, now=None):
    before = snapshot(chain)
    with pytest.raises(ValidationError) as exc:
        chain.submit_mined_block(block, now=now)
    assert exc.value.code == code, (exc.value.code, str(exc.value))
    assert snapshot(chain) == before, "rejected block must not change any state"


# ------------------------------------------------------------------ transactions
def test_invalid_signature_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    bob = Acct()
    tx = alice.tx(bob, 1)
    bad_sig = tx["signature"][:-4] + ("AAAA" if not tx["signature"].endswith("AAAA") else "BBBB")
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction({**tx, "signature": bad_sig})
    assert exc.value.code == "bad_signature"


def test_modified_transaction_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    bob, mallory = Acct(), Acct()
    tx = alice.tx(bob, 1)
    for field, value in [("amount", mai(20)), ("recipient", mallory.address), ("fee", mai("0.5")),
                         ("nonce", 1), ("timestamp", START + 5)]:
        if tx[field] == value:
            continue
        with pytest.raises(ValidationError) as exc:
            chain.submit_transaction({**tx, field: value})
        assert exc.value.code == "bad_signature", field


def test_transaction_signed_by_someone_else_but_claiming_sender_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    mallory, bob = Acct(), Acct()
    forged = mallory.tx(bob, 1)
    forged["sender"] = alice.address                     # claim to be alice
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(forged)
    assert exc.value.code == "bad_signature"


@pytest.mark.parametrize("mutation,code", [
    (lambda t: t.update(amount=True), "bad_type"),
    (lambda t: t.update(amount=5.0), "bad_type"),
    (lambda t: t.update(amount="5000000"), "bad_type"),
    (lambda t: t.update(fee=None), "bad_type"),
    (lambda t: t.update(amount=0), "out_of_range"),
    (lambda t: t.update(amount=-1), "out_of_range"),
    (lambda t: t.update(amount=2 ** 63), "out_of_range"),
    (lambda t: t.update(amount=2 ** 200), "out_of_range"),
    (lambda t: t.update(fee=999), "out_of_range"),                 # below minimum fee
    (lambda t: t.update(nonce=0), "out_of_range"),
    (lambda t: t.update(timestamp=-1), "out_of_range"),
    (lambda t: t.update(extra="x"), "bad_fields"),
    (lambda t: t.pop("signature"), "bad_fields"),
    (lambda t: t.update(txid="XYZ"), "bad_hash"),
    (lambda t: t.update(recipient="not-an-address"), "bad_address"),
    (lambda t: t.update(sender="not-an-address"), "bad_address"),
])
def test_malformed_transactions_rejected_statelessly(mutation, code):
    alice, bob = Acct(), Acct()
    tx = alice.tx(bob, 1)
    mutation(tx)
    with pytest.raises(ValidationError) as exc:
        C.check_transaction(tx, TEST)
    assert exc.value.code == code


def test_self_transfer_rejected():
    alice = Acct()
    with pytest.raises(ValidationError) as exc:
        C.check_transaction(alice.tx(alice, 1), TEST)
    assert exc.value.code == "self_transfer"


def test_oversized_transaction_rejected():
    tiny = dataclasses.replace(TEST, max_tx_bytes=100)
    alice, bob = Acct(), Acct()
    with pytest.raises(ValidationError) as exc:
        C.check_transaction(alice.tx(bob, 1), tiny)
    assert exc.value.code == "too_large"


def test_duplicate_transaction_rejected_in_mempool_and_after_confirmation(tmp_path):
    chain, alice = funded(tmp_path)
    bob = Acct()
    tx = alice.tx(bob, 1)
    chain.submit_transaction(tx)
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(tx)
    assert exc.value.code == "duplicate_tx"
    mine(chain, alice)
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(tx)
    assert exc.value.code == "duplicate_tx"          # already confirmed


def test_double_spend_same_nonce_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    bob, carol = Acct(), Acct()
    chain.submit_transaction(alice.tx(bob, 10))
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(alice.tx(carol, 10, nonce=1))     # conflicting tx, same nonce
    assert exc.value.code == "bad_nonce"


def test_double_spend_exceeding_balance_rejected(tmp_path):
    chain, alice = funded(tmp_path)               # 3 blocks: 75 MAI, of which 25 is still immature
    bob = Acct()
    avail = chain.account(alice.address)["available_balance"]
    chain.submit_transaction(alice.tx(bob, atomic=avail - mai("0.01") - 1))    # leaves less than the next tx needs
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(alice.tx(bob, 1, nonce=2))
    assert exc.value.code == "insufficient_funds"


def test_nonce_gap_and_replay_of_old_nonce_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    bob = Acct()
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(alice.tx(bob, 1, nonce=5))
    assert exc.value.code == "bad_nonce"
    chain.submit_transaction(alice.tx(bob, 1, nonce=1))
    mine(chain, alice)
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(alice.tx(bob, 2, nonce=1, timestamp=START + 1))   # nonce 1 already used
    assert exc.value.code == "bad_nonce"


def test_immature_coinbase_cannot_be_spent(tmp_path):
    chain = make_chain(tmp_path)
    alice, bob = Acct(), Acct()
    mine(chain, alice)                                     # height 1; maturity is 2
    chain.clock.advance(60)
    assert chain.account(alice.address)["available_balance"] == 0
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(alice.tx(bob, 1))
    assert exc.value.code == "insufficient_funds"
    mine(chain, alice)                                     # height 2
    chain.clock.advance(60)
    assert chain.account(alice.address)["available_balance"] == mai(25)     # block 1 matured
    chain.submit_transaction(alice.tx(bob, 25 - 1, fee="0.01"))


def test_block_spending_immature_coinbase_rejected(tmp_path):
    chain = make_chain(tmp_path)
    alice, bob = Acct(), Acct()
    mine(chain, alice)
    chain.clock.advance(60)
    # Mallory-style block: builder injects a spend of the immature coinbase directly.
    tx = alice.tx(bob, 5)

    def inject(block):
        block["transactions"].append(tx)
        fees = tx["fee"]
        block["transactions"][0] = C.coinbase_tx(chain.params, block["height"], alice.address,
                                                 chain.next_subsidy(), fees)
    reject(chain, build(chain, alice, inject), "insufficient_funds")


# ------------------------------------------------------------------ blocks
def test_valid_block_with_transaction_and_fees(tmp_path):
    chain, alice = funded(tmp_path)
    bob, miner = Acct(), Acct()
    chain.submit_transaction(alice.tx(bob, 5, fee="0.05"))
    block = mine(chain, miner)
    assert block["fees"] == mai("0.05") and block["subsidy"] == mai(25)
    assert chain.account(miner.address)["balance"] == mai("25.05")
    assert chain.account(bob.address)["balance"] == mai(5)
    assert chain.account(alice.address)["balance"] == mai(75) - mai(5) - mai("0.05")
    assert chain.storage.total_balances() == chain.minted_supply() == mai(100)      # fees are transfers, not mint
    chain.verify_integrity()


def test_invalid_previous_hash_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    reject(chain, build(chain, alice, lambda b: b.update(previous_hash="1" * 64)), "stale")


def test_wrong_height_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    reject(chain, build(chain, alice, lambda b: b.update(height=b["height"] + 1)), "stale")
    reject(chain, build(chain, alice, lambda b: b.update(height=b["height"] - 1)), "stale")


def test_invalid_proof_of_work_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    block = build(chain, alice)
    prefix = C.header_prefix(chain.params, block["height"], block["previous_hash"], block["merkle_root"],
                             block["timestamp"], block["difficulty"])
    nonce = 0
    while C.meets_target(C.hash_header(prefix, nonce), block["difficulty"]):
        nonce += 1
    block["nonce"], block["hash"] = nonce, C.hash_header(prefix, nonce)     # correct hash, insufficient work
    reject(chain, block, "bad_pow")


def test_wrong_hash_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    block = build(chain, alice)
    block["hash"] = "0" * 64
    reject(chain, block, "bad_hash")
    block = build(chain, alice)
    block["nonce"] += 1                                                         # hash no longer matches
    reject(chain, block, "bad_hash")


def test_incorrect_difficulty_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    reject(chain, build(chain, alice, lambda b: b.update(difficulty=2)), "bad_difficulty")      # easier than required
    reject(chain, build(chain, alice, lambda b: b.update(difficulty=17)), "bad_difficulty")     # harder than required
    with pytest.raises(ValidationError) as exc:                                                    # 0 is not a valid difficulty at all
        C.check_block_structure({**build(chain, alice), "difficulty": 0}, chain.params)
    assert exc.value.code == "out_of_range"


@pytest.mark.parametrize("subsidy_delta", [1, 1_000_000, -1])
def test_incorrect_mining_reward_rejected(tmp_path, subsidy_delta):
    chain, alice = funded(tmp_path)

    def cheat(block):
        block["transactions"][0] = C.coinbase_tx(chain.params, block["height"], alice.address,
                                                 chain.next_subsidy() + subsidy_delta, 0)
    reject(chain, build(chain, alice, cheat), "bad_coinbase")


def test_coinbase_claiming_unearned_fees_rejected(tmp_path):
    chain, alice = funded(tmp_path)

    def cheat(block):
        block["transactions"][0] = C.coinbase_tx(chain.params, block["height"], alice.address,
                                                 chain.next_subsidy(), mai(10))    # fees that do not exist
    reject(chain, build(chain, alice, cheat), "bad_coinbase")


def test_coinbase_missing_or_not_first_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    bob = Acct()
    tx = alice.tx(bob, 1)
    reject(chain, build(chain, alice, lambda b: b.update(transactions=[tx])), "bad_coinbase")
    # a coinbase in a non-first position is just a malformed regular transaction
    reject(chain, build(chain, alice, lambda b: b.update(transactions=[tx, b["transactions"][0]])),
           "bad_fields")


def test_second_coinbase_in_block_rejected(tmp_path):
    chain, alice = funded(tmp_path)

    def cheat(block):
        block["transactions"].append(dict(block["transactions"][0]))
    reject(chain, build(chain, alice, cheat), "bad_fields")


def test_duplicate_transaction_inside_block_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    tx = alice.tx(Acct(), 1)
    reject(chain, build(chain, alice, lambda b: b["transactions"].extend([tx, tx])), "duplicate_tx")


def test_already_confirmed_transaction_cannot_be_included_again(tmp_path):
    chain, alice = funded(tmp_path)
    tx = alice.tx(Acct(), 1)
    chain.submit_transaction(tx)
    mine(chain, alice)
    chain.clock.advance(60)
    def replay(block):
        block["transactions"].append(tx)
        block["transactions"][0] = C.coinbase_tx(chain.params, block["height"], alice.address,
                                                 chain.next_subsidy(), tx["fee"])
    reject(chain, build(chain, alice, replay), "duplicate_tx")


def test_block_with_bad_nonce_ordering_or_double_spend_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    bob, carol = Acct(), Acct()
    t1, t2 = alice.tx(bob, 5, nonce=1), alice.tx(carol, 5, nonce=1, timestamp=START + 1)   # same nonce
    def cheat(block):
        block["transactions"].extend([t1, t2])
        block["transactions"][0] = C.coinbase_tx(chain.params, block["height"], alice.address,
                                                 chain.next_subsidy(), t1["fee"] + t2["fee"])
    reject(chain, build(chain, alice, cheat), "bad_nonce")


def test_block_overspending_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    tx = alice.tx(Acct(), 500)                       # far more than the balance
    def cheat(block):
        block["transactions"].append(tx)
        block["transactions"][0] = C.coinbase_tx(chain.params, block["height"], alice.address,
                                                 chain.next_subsidy(), tx["fee"])
    reject(chain, build(chain, alice, cheat), "insufficient_funds")


def test_merkle_root_must_match_transactions(tmp_path):
    chain, alice = funded(tmp_path)
    block = build(chain, alice)
    block["merkle_root"] = "a" * 64
    prefix = C.header_prefix(chain.params, block["height"], block["previous_hash"], block["merkle_root"],
                             block["timestamp"], block["difficulty"])
    n = 0
    while not C.meets_target(C.hash_header(prefix, n), block["difficulty"]):
        n += 1
    block["nonce"], block["hash"] = n, C.hash_header(prefix, n)                # valid PoW, wrong merkle root
    reject(chain, block, "bad_merkle")


def test_oversized_block_rejected(tmp_path):
    small = dataclasses.replace(TEST, max_regular_txs_per_block=1)
    chain, alice = funded(tmp_path, params=small)
    bob = Acct(small)
    t1, t2 = alice.tx(bob, 1, nonce=1), alice.tx(bob, 1, nonce=2)
    def cheat(block):
        block["transactions"].extend([t1, t2])
    reject(chain, build(chain, alice, cheat), "too_large")

    tiny = dataclasses.replace(TEST, max_block_bytes=100)
    chain2 = make_chain(tmp_path, tiny, name="tiny.db")
    with pytest.raises(ValidationError) as exc:
        C.check_block_structure(build(chain2, Acct(tiny)), tiny)
    assert exc.value.code == "too_large"


@pytest.mark.parametrize("field,value,code", [
    ("timestamp", 2 ** 70, "out_of_range"), ("timestamp", -1, "out_of_range"), ("timestamp", "5", "bad_type"),
    ("nonce", -1, "out_of_range"), ("nonce", 2 ** 64, "out_of_range"), ("height", True, "bad_type"),
    ("height", 0, "out_of_range"), ("previous_hash", "ZZ", "bad_hash"), ("hash", "A" * 64, "bad_hash"),
    ("difficulty", 1.0, "bad_type"),
])
def test_malformed_block_fields_rejected(tmp_path, field, value, code):
    chain, alice = funded(tmp_path)
    block = build(chain, alice)
    block[field] = value
    reject(chain, block, code)


def test_extra_or_missing_block_fields_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    block = build(chain, alice)
    reject(chain, {**block, "extra": 1}, "bad_fields")
    missing = dict(block)
    missing.pop("nonce")
    reject(chain, missing, "bad_fields")
    reject(chain, "not a dict", "bad_fields")


# ------------------------------------------------------------------ timestamps
def test_timestamp_not_greater_than_median_rejected(tmp_path):
    chain, alice = funded(tmp_path, blocks=5)
    tip_ts = chain.tip()["timestamp"]
    old = build(chain, alice, lambda b: b.update(timestamp=1))
    reject(chain, old, "bad_timestamp")
    stale = build(chain, alice, lambda b: b.update(timestamp=tip_ts - 10 ** 6))
    reject(chain, stale, "bad_timestamp")


def test_timestamp_too_far_in_future_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    future = chain.now() + TEST.max_future_seconds + 1
    reject(chain, build(chain, alice, lambda b: b.update(timestamp=future)), "bad_timestamp")
    ok = chain.now() + TEST.max_future_seconds
    chain.submit_mined_block(build(chain, alice, lambda b: b.update(timestamp=ok)))       # boundary is allowed


def test_median_time_past_uses_the_median_not_the_last_block(tmp_path):
    chain = make_chain(tmp_path)
    alice = Acct()
    for delta in (0, 60, 120, 180, 240):
        mine(chain, alice, now=START + delta)
    stamps = sorted(chain.storage.recent_timestamps(11))
    mtp = C.median_time_past(chain.storage.recent_timestamps(11))
    assert mtp == stamps[len(stamps) // 2] if len(stamps) % 2 else True
    # a timestamp between the median and the last block is valid even though it is < tip timestamp
    tip_ts = chain.tip()["timestamp"]
    block = build(chain, alice, lambda b: b.update(timestamp=mtp + 1), now=START + 300)
    assert mtp + 1 < tip_ts
    chain.submit_mined_block(block, now=START + 300)


# ------------------------------------------------------------------ supply cap
def test_maximum_supply_boundary(tmp_path):
    capped = dataclasses.replace(TEST, max_supply=mai(40))
    chain = make_chain(tmp_path, capped)
    alice = Acct(capped)
    b1, b2 = mine(chain, alice), mine(chain, alice)
    assert (b1["subsidy"], b2["subsidy"]) == (mai(25), mai(15))
    assert chain.minted_supply() == mai(40) and chain.next_subsidy() == 0
    # cannot mint even 1 atomic unit more
    def cheat(block):
        block["transactions"][0] = C.coinbase_tx(capped, block["height"], alice.address, 1, 0)
    reject(chain, build(chain, alice, cheat), "bad_coinbase")
    # a zero-subsidy block is valid, and pays only fees
    chain.clock.advance(120)
    for _ in range(3):
        chain.clock.advance(60)
    b3 = mine(chain, alice)
    assert b3["subsidy"] == 0
    assert chain.minted_supply() == mai(40) == chain.storage.total_balances()
    chain.verify_integrity()


def test_supply_never_exceeds_cap_with_fees_flowing(tmp_path):
    capped = dataclasses.replace(TEST, max_supply=mai(60))
    chain = make_chain(tmp_path, capped)
    alice, bob = Acct(capped), Acct(capped)
    mine_n(chain, alice, 3)                                    # 25 + 25 + 10
    chain.submit_transaction(alice.tx(bob, 5))
    mine(chain, bob)                                           # bob earns only the fee
    assert chain.minted_supply() == mai(60)
    assert chain.storage.total_balances() == mai(60)


# ------------------------------------------------------------------ replay / network separation
def test_transaction_signed_for_another_network_is_rejected(tmp_path):
    other = dataclasses.replace(TEST, network_id="mineai-other-network")
    chain, alice = funded(tmp_path)
    foreign_alice = Acct(other)
    foreign_alice.key, foreign_alice.address = alice.key, alice.address       # same key, other network
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(foreign_alice.tx(Acct(), 1))
    assert exc.value.code == "bad_signature"


def test_replay_of_confirmed_transaction_on_a_second_chain_fails(tmp_path):
    a = dataclasses.replace(TEST, network_id="net-a", genesis_hash="")
    b = dataclasses.replace(TEST, network_id="net-b", genesis_hash="")
    (tmp_path / "a").mkdir()
    chain_a, alice_a = funded(tmp_path / "a", params=a)
    chain_b = make_chain(tmp_path, b, name="b.db")
    alice_b = Acct(b)
    alice_b.key, alice_b.address = alice_a.key, alice_a.address
    mine_n(chain_b, alice_b, 3)
    tx = alice_a.tx(Acct(a), 1)
    chain_a.submit_transaction(tx)
    with pytest.raises(ValidationError) as exc:
        chain_b.submit_transaction(tx)                        # same bytes, funded on both chains
    assert exc.value.code == "bad_signature"


def test_address_from_another_network_prefix_is_rejected(tmp_path):
    chain, alice = funded(tmp_path)
    testnet_addr = Acct(dataclasses.replace(TEST, address_prefix="TMAI")).address
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(alice.tx(testnet_addr, 1))
    assert exc.value.code == "bad_address"
    with pytest.raises(ValidationError) as exc:
        chain.mining_template(testnet_addr)
    assert exc.value.code == "bad_address"


# ------------------------------------------------------------------ mempool policy & template
def test_template_orders_by_fee_and_respects_nonce_order(tmp_path):
    chain, alice = funded(tmp_path, blocks=4)
    bob = Acct()
    chain.submit_transaction(alice.tx(bob, 1, fee="0.002", nonce=1))
    chain.submit_transaction(alice.tx(bob, 1, fee="0.5", nonce=2, timestamp=START + 1))   # higher fee, later nonce
    template = chain.mining_template(alice.address)
    nonces = [t["nonce"] for t in template["transactions"][1:]]
    assert nonces == [1, 2]                      # nonce 2 cannot precede nonce 1 despite its higher fee
    block = mine(chain, alice)
    assert len(block["transactions"]) == 3


def test_mempool_is_pruned_of_transactions_that_became_invalid(tmp_path):
    chain, alice = funded(tmp_path)
    tx = alice.tx(Acct(), 1)
    # a block, built by "another node" that never saw `tx`, confirms nonce 1 via a *different* tx
    other = alice.tx(Acct(), 2, timestamp=START + 3)
    def other_node_block(block):
        block["transactions"].append(other)
        block["transactions"][0] = C.coinbase_tx(chain.params, block["height"], alice.address,
                                                 chain.next_subsidy(), other["fee"])
    block = build(chain, alice, other_node_block)            # mempool is still empty here
    chain.submit_transaction(tx)                             # ...and our node learns `tx` afterwards
    assert chain.storage.mempool_count() == 1
    chain.submit_mined_block(block)
    assert chain.storage.mempool_count() == 0                # tx (nonce 1) is now stale and was pruned


def test_mempool_limits(tmp_path):
    limited = dataclasses.replace(TEST, mempool_max_txs=3, mempool_max_per_sender=2)
    chain = make_chain(tmp_path, limited)
    alice, bob = Acct(limited), Acct(limited)
    mine_n(chain, alice, 5)
    chain.submit_transaction(alice.tx(bob, 1, nonce=1))
    chain.submit_transaction(alice.tx(bob, 1, nonce=2, timestamp=START + 1))
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(alice.tx(bob, 1, nonce=3, timestamp=START + 2))
    assert exc.value.code == "mempool_sender_limit"


def test_mempool_full_rejects_new_transactions(tmp_path):
    limited = dataclasses.replace(TEST, mempool_max_txs=1)
    chain = make_chain(tmp_path, limited)
    alice, bob = Acct(limited), Acct(limited)
    mine_n(chain, alice, 5)
    chain.submit_transaction(alice.tx(bob, 1))
    with pytest.raises(ValidationError) as exc:
        chain.submit_transaction(alice.tx(bob, 1, nonce=2, timestamp=START + 1))
    assert exc.value.code == "mempool_full"


def test_mempool_rejects_wildly_wrong_transaction_timestamps(tmp_path):
    chain, alice = funded(tmp_path)
    for ts in (0, START + 10 ** 7, 2 ** 62):
        with pytest.raises(ValidationError) as exc:
            chain.submit_transaction(alice.tx(Acct(), 1, timestamp=ts))
        assert exc.value.code == "bad_timestamp"


def test_account_report_fields(tmp_path):
    chain, alice = funded(tmp_path)
    info = chain.account(alice.address)
    assert info["balance"] == mai(75) and info["immature_balance"] == mai(25)
    assert info["available_balance"] == mai(50) and info["next_nonce"] == 1
    chain.submit_transaction(alice.tx(Acct(), 10))
    info = chain.account(alice.address)
    assert info["pending_outgoing"] == mai("10.01") and info["next_nonce"] == 2
    assert info["available_balance"] == mai(50) - mai("10.01")
    with pytest.raises(ValidationError):
        chain.account("nope")
