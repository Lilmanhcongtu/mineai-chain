"""Fork choice and reorganizations at the chain level (no networking)."""
from __future__ import annotations

import dataclasses
import random

import pytest

from mineai import consensus as C
from mineai.blockchain import Blockchain
from mineai.consensus import ValidationError
from mineai.storage import WIRE_KEYS
from tests.helpers import START, TEST, Acct, Clock, build, make_chain, mai, mine, solve


def wire(block: dict) -> dict:
    return {k: block[k] for k in WIRE_KEYS}


def clone(src: Blockchain, tmp_path, name: str, upto: int | None = None) -> Blockchain:
    """A scratch node holding a copy of `src`'s best chain (up to a height); shares the clock."""
    dst = make_chain(tmp_path, src.params, src.clock, name=name)
    for b in src.storage.blocks_after(0, upto if upto is not None else src.tip()["height"]):
        dst.submit_mined_block(wire(b))
    return dst


def extend(chain: Blockchain, miner, n: int) -> list[dict]:
    out = []
    for _ in range(n):
        out.append(wire(mine(chain, miner)))
        chain.clock.advance(60)
    return out


def snap(chain: Blockchain):
    st = chain.storage
    accounts = st.conn.execute("SELECT address, balance, nonce FROM accounts ORDER BY address").fetchall()
    return (chain.tip()["hash"], chain.minted_supply(), st.total_balances(), [tuple(r) for r in accounts],
            [t["txid"] for t in st.mempool_list()], chain.reorg_count)


def new_node(tmp_path, name="n.db", params=TEST, miner=None, blocks=0):
    chain = make_chain(tmp_path, params, name=name)
    miner = miner or Acct(params)
    if blocks:
        extend(chain, miner, blocks)
    return chain, miner


def check_invariants(chain: Blockchain):
    assert chain.storage.total_balances() == chain.minted_supply()
    chain.verify_integrity()


# ------------------------------------------------------------------ competing blocks
def test_competing_block_is_stored_as_side_and_first_seen_stays_tip(tmp_path):
    n, alice = new_node(tmp_path, blocks=2)
    y = clone(n, tmp_path, "y.db", upto=1)
    rival = extend(y, Acct(), 1)[0]                                # a different block 2
    tip_before = n.tip()["hash"]
    result = n.process_block(rival)
    assert result.status == "side" and n.tip()["hash"] == tip_before and n.storage.side_count() == 1
    assert n.reorg_count == 0
    assert n.process_block(rival).status == "duplicate"
    check_invariants(n)


def test_two_miners_find_a_block_at_the_same_time_then_converge(tmp_path):
    a, alice = new_node(tmp_path, "a.db", blocks=1)
    b = clone(a, tmp_path, "b.db")
    block_a = wire(mine(a, alice)); a.clock.advance(60)            # both extend height 1 "simultaneously"
    bob = Acct()
    block_b = wire(mine(b, bob))
    assert block_a["hash"] != block_b["hash"]
    assert a.process_block(block_b).status == "side" and b.process_block(block_a).status == "side"
    assert a.tip()["hash"] != b.tip()["hash"]                       # split, equal work: each keeps its first-seen block
    nxt = wire(mine(a, alice)); a.clock.advance(60)                 # a finds the next block
    result = b.process_block(nxt)                                   # its parent (block_a) is a stored side block
    assert result.status == "reorg" and (result.disconnected, result.connected) == (1, 2)
    assert a.tip()["hash"] == b.tip()["hash"]
    for chain in (a, b):
        check_invariants(chain)
    assert b.account(bob.address)["balance"] == 0 and b.account(alice.address)["balance"] == mai(75)


# ------------------------------------------------------------------ cumulative work vs length
def test_deeper_valid_chain_replaces_the_local_chain(tmp_path):
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db", upto=1)
    bob = Acct()
    branch = extend(y, bob, 4)                                     # fork at height 1, ends at height 5
    statuses = [n.process_block(b).status for b in branch]
    # blocks 2' and 3' tie/trail the local chain (work 2, 3 vs 3); 4' overtakes it; 5' just extends the new tip
    assert statuses == ["side", "side", "reorg", "extended"]
    assert n.tip()["hash"] == branch[-1]["hash"] and n.tip()["height"] == 5
    assert n.account(alice.address)["balance"] == mai(25)          # only block 1 remains hers
    assert n.account(bob.address)["balance"] == mai(100)
    check_invariants(n)


def test_shorter_chain_with_more_cumulative_work_wins(tmp_path):
    rules: dict[str, int] = {}
    hook = lambda parent: rules.get(parent["hash"], 1)
    n, alice = new_node(tmp_path, blocks=3)                        # long chain: 3 blocks of difficulty 1 (work 48)
    n.expected_difficulty = hook
    y = make_chain(tmp_path, TEST, n.clock, name="y.db")
    y.expected_difficulty = hook
    genesis = n.storage.get_block_by_height(0)["hash"]
    rules[genesis] = 2                                             # from now on, blocks on genesis need difficulty 2
    hard = wire(mine(y, Acct()))                                   # ONE block of difficulty 2 (work 256)
    assert hard["difficulty"] == 2 and n.tip()["height"] == 3
    result = n.process_block(hard)
    assert result.status == "reorg"
    assert n.tip()["hash"] == hard["hash"] and n.tip()["height"] == 1     # shorter, but heavier
    assert n.tip_work() == 256 > 48
    assert n.account(alice.address)["balance"] == 0
    check_invariants(n)


def test_equal_work_never_causes_switching(tmp_path):
    n, _ = new_node(tmp_path, blocks=2)
    y = clone(n, tmp_path, "y.db", upto=0)
    rival = extend(y, Acct(), 2)
    tip = n.tip()["hash"]
    for b in rival:
        assert n.process_block(b).status == "side"
    assert n.tip()["hash"] == tip and n.reorg_count == 0


# ------------------------------------------------------------------ transactions across reorganizations
def test_transaction_on_the_abandoned_branch_returns_to_the_mempool(tmp_path):
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db")
    bob, carol = Acct(), Acct()
    tx = alice.tx(bob, 5, nonce=1, timestamp=n.now())
    n.submit_transaction(tx)
    mine(n, alice); n.clock.advance(60)                            # tx confirmed in block 4 (old branch)
    assert n.find_transaction(tx["txid"])["status"] == "confirmed"
    branch = extend(y, carol, 2)                                   # competing blocks 4', 5' without the tx
    results = [n.process_block(b).status for b in branch]
    assert results[-1] == "reorg"
    found = n.find_transaction(tx["txid"])
    assert found["status"] == "mempool"                            # back in the mempool
    assert n.account(bob.address)["balance"] == 0
    mined = mine(n, carol)                                         # ...and can be confirmed again
    assert n.find_transaction(tx["txid"])["status"] == "confirmed" and n.account(bob.address)["balance"] == mai(5)
    check_invariants(n)


def test_conflicting_transaction_on_the_new_branch_wins(tmp_path):
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db")
    bob, carol = Acct(), Acct()
    old_tx = alice.tx(bob, 5, nonce=1, timestamp=n.now())
    n.submit_transaction(old_tx)
    mine(n, alice); n.clock.advance(60)
    new_tx = alice.tx(carol, 7, nonce=1, timestamp=y.now() + 1)    # same sender+nonce, different payment
    y.submit_transaction(new_tx)
    b4 = wire(mine(y, carol)); y.clock.advance(60)
    b5 = wire(mine(y, carol)); y.clock.advance(60)
    n.process_block(b4)
    assert n.process_block(b5).status == "reorg"
    assert n.account(carol.address)["balance"] >= mai(7)
    assert n.account(bob.address)["balance"] == 0
    assert n.find_transaction(old_tx["txid"]) is None              # cannot return: its nonce was used
    assert n.storage.mempool_count() == 0
    check_invariants(n)


def test_dependent_transactions_return_in_nonce_order(tmp_path):
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db")
    bob = Acct()
    t1 = alice.tx(bob, 1, nonce=1, timestamp=n.now())
    t2 = alice.tx(bob, 2, nonce=2, timestamp=n.now() + 1)
    n.submit_transaction(t1); n.submit_transaction(t2)
    mine(n, alice); n.clock.advance(60)
    for b in extend(y, Acct(), 2):
        n.process_block(b)
    assert {t["txid"] for t in n.storage.mempool_list()} == {t1["txid"], t2["txid"]}
    assert [t["nonce"] for t in n.storage.mempool_for_sender(alice.address)] == [1, 2]
    check_invariants(n)


# ------------------------------------------------------------------ invalid branches
def craft_overspend(chain, miner, victim: Acct):
    tx = victim.tx(Acct(), 1000, nonce=1, timestamp=START)
    def cheat(block):
        block["transactions"].append(tx)
        block["transactions"][0] = C.coinbase_tx(chain.params, block["height"], miner.address,
                                                 chain.next_subsidy(), tx["fee"])
    return wire(build(chain, miner, cheat))


def test_invalid_high_work_branch_is_rejected_and_state_is_untouched(tmp_path):
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db", upto=1)
    mallory = Acct()
    good = extend(y, mallory, 2)                                   # valid 2', 3' (equal work to n's tip)
    bad = craft_overspend(y, mallory, alice)                       # 4': PoW fine, spends money it lacks
    for b in good:
        assert n.process_block(b).status == "side"
    before = snap(n)
    with pytest.raises(ValidationError) as exc:
        n.process_block(bad)                                       # most work, but invalid -> reorg attempted, rolled back
    assert exc.value.code == "insufficient_funds"
    assert snap(n) == before                                       # nothing changed at all
    assert n.storage.side_get(bad["hash"])["status"] == "invalid"
    assert n.storage.side_get(good[0]["hash"])["status"] == "ok"
    with pytest.raises(ValidationError) as again:
        n.process_block(bad)
    assert again.value.code == "invalid_block"                     # never re-evaluated
    check_invariants(n)


def test_descendants_of_an_invalid_block_are_rejected(tmp_path):
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db", upto=1)
    mallory = Acct()
    good = extend(y, mallory, 2)
    bad = craft_overspend(y, mallory, alice)
    for b in good:
        n.process_block(b)
    with pytest.raises(ValidationError):
        n.process_block(bad)
    z = clone(n, tmp_path, "z.db", upto=1)
    for b in good:
        z.submit_mined_block(b)
    # craft a child of `bad` by hand: reuse its hash as parent
    child_block = build(z, mallory, lambda blk: blk.update(previous_hash=bad["hash"], height=bad["height"] + 1))
    with pytest.raises(ValidationError) as exc:
        n.process_block(wire(child_block))
    assert exc.value.code == "invalid_parent"


def test_reorg_is_atomic_when_the_process_dies_midway(tmp_path, monkeypatch):
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db", upto=1)
    branch = extend(y, Acct(), 3)
    for b in branch[:-1]:
        n.process_block(b)
    before = snap(n)
    calls = {"n": 0}
    real = n.storage.side_remove

    def dying(h):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash in the middle of a reorganization")
        real(h)
    monkeypatch.setattr(n.storage, "side_remove", dying)
    with pytest.raises(RuntimeError):
        n.process_block(branch[-1])
    monkeypatch.undo()
    assert snap(n) == before                                       # old chain fully intact, no half-applied reorg
    check_invariants(n)
    assert n.process_block(branch[-1]).status == "reorg"           # the stored heavier branch is retried
    assert n.tip()["hash"] == branch[-1]["hash"]
    check_invariants(n)


def test_time_dependent_failure_does_not_permanently_ban_a_branch(tmp_path):
    n, alice = new_node(tmp_path, blocks=2)
    y = clone(n, tmp_path, "y.db", upto=0)
    branch = extend(y, Acct(), 3)
    far = n.now() + TEST.max_future_seconds
    for b in branch[:2]:
        n.process_block(b)
    # rebuild y's third block with a timestamp that n considers too far in the future
    y2 = clone(n, tmp_path, "y2.db", upto=0)
    for b in branch[:2]:
        y2.submit_mined_block(b)
    future_block = wire(build(y2, Acct(), lambda b: b.update(timestamp=far + 5000), now=far + 5000))
    with pytest.raises(ValidationError) as exc:
        n.process_block(future_block)
    assert exc.value.code == "bad_timestamp"
    assert n.storage.side_get(future_block["hash"])["status"] == "ok"          # not marked invalid
    n.clock.advance(5000 + 60)                                                 # time passes: it becomes valid
    assert n.process_block(future_block).status in ("reorg", "duplicate")
    assert n.tip()["hash"] == future_block["hash"]


# ------------------------------------------------------------------ policy limits
def test_reorganizations_deeper_than_the_limit_are_refused(tmp_path):
    shallow = dataclasses.replace(TEST, max_reorg_depth=3)
    n, alice = new_node(tmp_path, params=shallow, blocks=6)
    y = clone(n, tmp_path, "y.db", upto=1)                         # fork 5 blocks below n's tip
    branch = extend(y, Acct(shallow), 8)                           # ...and much heavier
    before = snap(n)
    with pytest.raises(ValidationError) as exc:
        n.process_block(branch[0])
    assert exc.value.code == "reorg_too_deep"
    assert snap(n) == before and n.storage.side_count() == 0       # nothing stored for hopeless forks
    y2 = clone(n, tmp_path, "y2.db", upto=4)                       # a fork only 2 deep is fine
    ok = extend(y2, Acct(shallow), 3)
    for b in ok:
        n.process_block(b)
    assert n.tip()["hash"] == ok[-1]["hash"]
    check_invariants(n)


def test_side_blocks_are_pruned_by_depth_and_count(tmp_path):
    small = dataclasses.replace(TEST, max_side_blocks=3, max_reorg_depth=50)
    n, _ = new_node(tmp_path, params=small, blocks=1)
    for i in range(6):
        y = clone(n, tmp_path, f"y{i}.db", upto=0)
        n.process_block(extend(y, Acct(small), 1)[0])
    assert n.storage.side_count() <= 3


def test_orphans_are_held_then_adopted_when_the_parent_arrives(tmp_path):
    n, _ = new_node(tmp_path)
    y = clone(n, tmp_path, "y.db")
    branch = extend(y, Acct(), 4)
    for b in reversed(branch[1:]):
        assert n.process_block(b).status == "orphan"
    assert n.tip()["height"] == 0 and len(n.orphans) == 3
    assert n.process_block(branch[0]).status == "extended"         # the missing parent unlocks the chain
    assert n.tip()["hash"] == branch[-1]["hash"] and len(n.orphans) == 0
    check_invariants(n)


def test_orphan_pool_is_bounded(tmp_path):
    tiny = dataclasses.replace(TEST, max_orphans=5)
    n, _ = new_node(tmp_path, params=tiny)
    y = clone(n, tmp_path, "y.db")
    branch = extend(y, Acct(tiny), 30)
    for b in branch[1:]:
        assert n.process_block(b).status == "orphan"
    assert len(n.orphans) == 5


def test_invalid_orphans_are_dropped_not_kept_forever(tmp_path):
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db", upto=1)
    mallory = Acct()
    good = extend(y, mallory, 1)
    bad = craft_overspend(y, mallory, alice)
    assert n.process_block(bad).status == "orphan"
    n.process_block(good[0])
    assert len(n.orphans) == 0                                     # tried once, dropped


# ------------------------------------------------------------------ persistence and flip-flopping
def test_reorg_survives_restart_and_abandoned_branch_can_win_again(tmp_path):
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db", upto=1)
    heavy = extend(y, Acct(), 3)
    for b in heavy:
        n.process_block(b)
    assert n.tip()["hash"] == heavy[-1]["hash"] and n.reorg_count == 1
    n.close()
    n = Blockchain(tmp_path / "n.db", TEST, clock=y.clock)
    assert n.tip()["hash"] == heavy[-1]["hash"]
    check_invariants(n)
    assert n.storage.side_count() == 2                             # the abandoned blocks 2, 3 are still stored
    # extend the abandoned branch (alice's blocks 2, 3) until it outweighs the current one
    old = clone(n, tmp_path, "old.db", upto=1)
    olds = [wire(n.storage.side_get(h)["block"]) for h in [
        r[0] for r in n.storage.conn.execute("SELECT hash FROM side_blocks ORDER BY height").fetchall()]]
    for b in olds:
        old.submit_mined_block(b)
    more = extend(old, alice, 3)
    for b in more:
        n.process_block(b)
    assert n.tip()["hash"] == more[-1]["hash"] and n.reorg_count == 1      # (counter is per process: reset by the restart)
    check_invariants(n)


def test_supply_and_balances_stay_consistent_across_repeated_reorgs(tmp_path):
    n, alice = new_node(tmp_path, blocks=2)
    rng = random.Random(7)
    for i in range(6):
        y = clone(n, tmp_path, f"y{i}.db", upto=rng.choice([0, 1]))
        miner = Acct()
        need = n.tip()["height"] - y.tip()["height"] + 1
        for b in extend(y, miner, need):
            try:
                n.process_block(b)
            except ValidationError:
                pass
        check_invariants(n)


# ------------------------------------------------------------------ convergence property
@pytest.mark.parametrize("seed", range(30))
def test_nodes_receiving_the_same_blocks_in_any_order_converge(tmp_path, seed):
    rng = random.Random(seed)
    base, _ = new_node(tmp_path, "base.db", blocks=2)
    blocks: dict[str, dict] = {}
    branches = []
    # Branch 0 is built first and is strictly the heaviest (height >= 8). Every other branch grows from the
    # shared base or from other non-winning branches and is capped at height 7, so the best chain is unique
    # (equal-work ties legitimately depend on arrival order and are not part of this property).
    for i in range(5):
        if i == 0:
            src, upto, length = base, rng.randint(0, 2), 8
        else:
            src = base if len(branches) == 1 or rng.random() < 0.4 else branches[rng.randrange(1, len(branches))]
            upto = rng.randint(0, min(src.tip()["height"], 6))
            length = rng.randint(1, 7 - upto)
        y = clone(src, tmp_path, f"b{i}.db", upto=upto)
        for blk in extend(y, Acct(), length):
            blocks[blk["hash"]] = blk
        branches.append(y)
    winner = branches[0]
    assert all(y.tip_work() < winner.tip_work() for y in branches[1:])
    nodes = []
    for k in range(3):
        node = make_chain(tmp_path, TEST, base.clock, name=f"node{k}.db")
        for b in base.storage.blocks_after(0, base.tip()["height"]):
            node.submit_mined_block(wire(b))
        order = list(blocks.values())
        random.Random(seed * 100 + k).shuffle(order)
        for b in order:
            try:
                node.process_block(b)
            except ValidationError:
                pass
        nodes.append(node)
    for node in nodes:
        assert node.tip()["hash"] == winner.tip()["hash"], f"seed {seed}"
        check_invariants(node)


# ------------------------------------------------------------------ locator
def test_locator_shape_and_use(tmp_path):
    n, _ = new_node(tmp_path, blocks=40)
    loc = n.locator()
    assert loc[0] == n.tip()["hash"] and loc[-1] == n.storage.get_block_by_height(0)["hash"]
    assert len(loc) == len(set(loc)) <= 32 and len(loc) < 40       # sparse, not one hash per block
    y = clone(n, tmp_path, "y.db", upto=25)
    extend(y, Acct(), 5)                                           # y: same to height 25, then its own 5 blocks
    got = n.blocks_after_locator(y.locator(), 64)
    assert got[0]["height"] == 26 and got[0]["hash"] == n.storage.get_block_by_height(26)["hash"]
    assert len(n.blocks_after_locator(["ab" * 32], 5)) == 5        # unknown locator -> from height 1
    assert n.blocks_after_locator(["ab" * 32], 5)[0]["height"] == 1
    assert len(n.blocks_after_locator(n.locator(), 64)) == 0       # already in sync
