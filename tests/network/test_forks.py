"""Forks and reorganizations across real P2P connections: every honest node must converge."""
from __future__ import annotations

import asyncio
import dataclasses
import os

from tests.consensus.test_forks import clone, extend, snap, wire
from tests.helpers import START, TEST, Acct, mine
from tests.network.harness import Cluster, Raw, addr, isolate, rejoin, run, tips, until

BLOCK_KEYS = ("height", "previous_hash", "merkle_root", "timestamp", "difficulty", "nonce", "hash", "transactions")


def announce(node, block):
    node.announce_block(block)


def test_two_miners_find_a_block_at_the_same_time_and_the_network_converges(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            d = await c.start("c", seeds=[addr(a)])
            await until(lambda: all(len(n.peers) >= 1 for n in (a, b, d)))
            alice, bob = Acct(), Acct()
            block_a = c.mine(a, alice, announce=False)                    # found "at the same time"
            block_b = c.mine(b, bob, announce=False)
            a.announce_block(block_a)
            b.announce_block(block_b)
            await until(lambda: a.chain.has_block(block_b["hash"]) and b.chain.has_block(block_a["hash"]),
                        msg="both blocks propagated")
            assert a.chain.tip()["hash"] != b.chain.tip()["hash"]         # a genuine split at equal work
            c.mine(a, alice)                                              # the next block settles it
            await until(lambda: len(tips(a, b, d)) == 1 and a.chain.tip()["height"] == 2, msg="convergence")
            assert a.chain.tip()["hash"] == tips(a, b, d).pop()
            assert b.chain.reorg_count + d.chain.reorg_count >= 1        # someone had to switch
            for n in (a, b, d):
                assert n.chain.storage.total_balances() == n.chain.minted_supply()
                n.chain.verify_integrity()
            assert d.chain.account(bob.address)["balance"] == 0          # bob's block lost the race
    run(scenario())


def test_repeated_races_never_leave_the_network_split(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            nodes = [await c.start("a"), None, None]
            nodes[1] = await c.start("b", seeds=[addr(nodes[0])])
            nodes[2] = await c.start("c", seeds=[addr(nodes[0])])
            await until(lambda: all(len(n.peers) >= 1 for n in nodes))
            miners = [Acct() for _ in nodes]
            for round_ in range(4):
                blocks = [c.mine(n, m, announce=False) for n, m in zip(nodes, miners)]   # three-way race
                for n, blk in zip(nodes, blocks):
                    n.announce_block(blk)
                # (Side blocks are deliberately not relayed, so a node need not see every losing block.)
                await asyncio.sleep(0.3)
                c.mine(nodes[round_ % 3], miners[round_ % 3])                # someone extends, ending the race
                await until(lambda: len(tips(*nodes)) == 1, msg=f"round {round_}")
            for n in nodes:
                n.chain.verify_integrity()
                assert n.chain.storage.total_balances() == n.chain.minted_supply()
    run(scenario())


def test_a_node_with_an_outdated_chain_reorganizes_when_it_reconnects(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            d = await c.start("c", seeds=[addr(a)])
            b = await c.start("b")                                       # not connected to anyone
            await until(lambda: len(a.peers) == 1)
            loner = Acct()
            for _ in range(2):
                c.mine(b, loner, announce=False)                         # b's own 2-block chain
            alice = Acct()
            for _ in range(6):
                c.mine(a, alice)
            await until(lambda: d.chain.tip()["height"] == 6)
            assert b.chain.tip()["height"] == 2 and b.chain.account(loner.address)["balance"] == 50_000_000
            b2 = await rejoin(c, "b", await _drop(c, b), [addr(a)])       # b comes online and connects
            await until(lambda: b2.chain.tip()["hash"] == a.chain.tip()["hash"], msg="b catches up")
            assert b2.chain.reorg_count >= 1
            assert b2.chain.account(loner.address)["balance"] == 0        # its abandoned coinbases are gone
            assert len(tips(a, b2, d)) == 1
            b2.chain.verify_integrity()
    run(scenario())


async def _drop(cluster, node):
    await node.stop()
    return node


def test_a_transaction_on_the_abandoned_branch_is_restored_and_confirmed_again(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            alice, bob, carol = Acct(), Acct(), Acct()
            for _ in range(3):
                c.mine(a, alice)
            await until(lambda: b.chain.tip()["height"] == 3)
            await isolate(c, b)                                           # b goes offline
            tx = alice.tx(bob, 5, nonce=1, timestamp=c.clock())
            b.chain.submit_transaction(tx)
            c.mine(b, carol, announce=False)                              # b confirms the tx in ITS block 4
            assert b.chain.find_transaction(tx["txid"])["status"] == "confirmed"
            for _ in range(3):
                c.mine(a, alice)                                          # meanwhile a builds a longer chain (no tx)
            b2 = await rejoin(c, "b", b, [addr(a)])
            await until(lambda: b2.chain.tip()["hash"] == a.chain.tip()["hash"])
            found = b2.chain.find_transaction(tx["txid"])
            assert found and found["status"] == "mempool"                 # returned to the mempool by the reorg
            assert b2.chain.account(bob.address)["balance"] == 0
            c.mine(b2, carol)                                             # anyone can now confirm it again
            await until(lambda: a.chain.tip()["hash"] == b2.chain.tip()["hash"])
            assert a.chain.account(bob.address)["balance"] == 5_000_000
    run(scenario())


def test_partition_and_heal_the_heaviest_chain_wins_everywhere(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            d = await c.start("c", seeds=[addr(b)])
            await until(lambda: all(len(n.peers) >= 1 for n in (a, b, d)))
            base = Acct()
            for _ in range(2):
                c.mine(a, base)
            await until(lambda: len(tips(a, b, d)) == 1 and a.chain.tip()["height"] == 2)
            await isolate(c, a)                                           # a is cut off from b and c
            minority, majority = Acct(), Acct()
            for _ in range(5):
                c.mine(a, minority, announce=False)                       # a mines 5 blocks alone (heaviest)
            for _ in range(3):
                c.mine(b, majority)                                       # b (and c) mine 3 blocks
            await until(lambda: d.chain.tip()["height"] == 5)
            assert a.chain.tip()["hash"] != b.chain.tip()["hash"]         # partitioned: genuinely different chains
            a2 = await rejoin(c, "a", a, [addr(b)])                       # partition heals
            await until(lambda: len(tips(a2, b, d)) == 1, timeout=15, msg="all converge")
            assert a2.chain.tip()["height"] == 7                          # a's heavier chain won
            assert b.chain.reorg_count >= 1 and d.chain.reorg_count >= 1
            for n in (a2, b, d):
                n.chain.verify_integrity()
                assert n.chain.account(majority.address)["balance"] == 0
    run(scenario())


def test_stopping_a_node_in_the_middle_of_a_reorganizing_sync_is_safe(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b")
            for _ in range(8):
                c.mine(b, Acct(), announce=False)                         # b's private fork
            alice = Acct()
            for _ in range(150):
                c.mine(a, alice, announce=False)                          # a: much longer, needs >2 sync batches
            b2 = await rejoin(c, "b", await _drop(c, b), [addr(a)])
            await until(lambda: b2.chain.tip()["height"] >= 20, timeout=15)
            await b2.stop()                                                # cut off somewhere in the middle
            b2.chain.verify_integrity()                                    # whatever it reached is consistent
            assert b2.chain.storage.total_balances() == b2.chain.minted_supply()
            b3 = await rejoin(c, "b", b2, [addr(a)])
            await until(lambda: b3.chain.tip()["hash"] == a.chain.tip()["hash"], timeout=30)
            b3.chain.verify_integrity()
    run(scenario())


# ====================================================================== hostile peers
def test_invalid_high_work_chain_from_a_peer_is_rejected_and_the_peer_banned(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            alice = Acct()
            for _ in range(3):
                c.mine(a, alice, announce=False)
            scratch = clone(a.chain, tmp_path, "scratch.db", upto=1)
            mallory = Acct()
            good = extend(scratch, mallory, 2)
            bad_tx = alice.tx(Acct(), 1000, nonce=1, timestamp=START)
            from mineai import consensus as C
            from tests.helpers import build

            def cheat(block):
                block["transactions"].append(bad_tx)
                block["transactions"][0] = C.coinbase_tx(scratch.params, block["height"], mallory.address,
                                                         scratch.next_subsidy(), bad_tx["fee"])
            bad = wire(build(scratch, mallory, cheat))
            before = snap(a.chain)
            ident = os.urandom(16).hex()
            raw = Raw(a, ident)
            await raw.connect(height=4, total_work=str(4 * 16))           # claims more work than a has
            await raw.recv_type("get_blocks")
            await raw.send("blocks", {"blocks": good + [bad]})
            assert await raw.closed(timeout=3.0)
            assert a.is_banned(ident)
            assert snap(a.chain) == before                                # a's chain, balances and mempool untouched
            a.chain.verify_integrity()
    run(scenario())


def test_side_blocks_are_not_announced_but_new_tips_are(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            alice = Acct()
            for _ in range(2):
                c.mine(a, alice, announce=False)
            watcher = await Raw(a).connect()
            scratch = clone(a.chain, tmp_path, "s.db", upto=1)
            rival = extend(scratch, Acct(), 3)                            # fork at 1: 2' (tie), 3' (heavier), 4'
            feeder = await Raw(a).connect()
            await feeder.send("block", {"block": rival[0]})               # equal work -> stored as a side block
            assert "inv" not in await watcher.drain(0.5)
            assert a.chain.has_block(rival[0]["hash"]) and a.chain.tip()["height"] == 2
            await feeder.send("block", {"block": rival[1]})               # now heavier -> reorg -> new tip announced
            inv = await watcher.recv_type("inv")
            assert inv["kind"] == "block" and inv["hashes"] == [rival[1]["hash"]]
            assert a.chain.tip()["hash"] == rival[1]["hash"]
    run(scenario())


def test_forks_deeper_than_the_limit_are_dropped_without_punishing_the_peer(tmp_path):
    async def scenario():
        shallow = dataclasses.replace(TEST, max_reorg_depth=2)
        async with Cluster(tmp_path, params=shallow) as c:
            a = await c.start("a")
            for _ in range(6):
                c.mine(a, Acct(shallow), announce=False)
            scratch = clone(a.chain, tmp_path, "s.db", upto=1)
            heavy = extend(scratch, Acct(shallow), 8)                     # fork 5 blocks below a's tip
            tip = a.chain.tip()["hash"]
            raw = await Raw(a).connect()
            await raw.send("block", {"block": heavy[0]})
            await asyncio.sleep(0.4)
            assert a.chain.tip()["hash"] == tip and a.chain.storage.side_count() == 0
            assert next(iter(a.peers.values())).score == 0                # a deep fork is not misbehavior
            assert not await raw.closed(timeout=0.3)
    run(scenario())


def test_orphan_block_triggers_a_locator_sync(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            src = await c.start("src")
            for _ in range(4):
                c.mine(src, Acct(), announce=False)
            raw = await Raw(a).connect()
            tip = src.chain.tip()
            await raw.send("block", {"block": {k: tip[k] for k in BLOCK_KEYS}})     # height 4, parent unknown
            get = await raw.recv_type("get_blocks")
            assert get["locator"][0] == a.chain.tip()["hash"] and get["count"] == 64
            blocks = src.chain.blocks_after_locator(get["locator"], 64)
            await raw.send("blocks", {"blocks": blocks})
            await until(lambda: a.chain.tip()["hash"] == src.chain.tip()["hash"])
            assert len(a.chain.orphans) == 0
    run(scenario())
