"""Honest traffic bursts must never get honest peers banned.

Found by the private testnet: a burst of ~170 relayed transactions made honest nodes exceed their per-peer message
rate limit; dropped messages were scored, scores never decayed, and honest nodes banned each other for 10 minutes,
splitting the network.
"""
from __future__ import annotations

import asyncio
import os
import time

from mineai.p2p import protocol as P
from tests.helpers import Acct
from tests.network.harness import Cluster, Raw, addr, frame, run, until


def total(node, metric: str) -> float:
    return node.chain.metrics.total(metric)


def test_an_honest_burst_of_messages_is_not_punished(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            raw = await Raw(a).connect()
            ping = P.encode(1, "ping", {"nonce": 1}, 1000)
            await raw.send_raw(ping * 800)                                # 800 messages in one write: an honest burst
            await asyncio.sleep(0.8)
            peer = next(p for p in a.peers.values() if p.node_id == raw.node_id)
            assert peer.score == 0 and total(a, "p2p_penalty_points") == 0 and not raw.writer.is_closing()
            assert not await raw.closed(timeout=0.3)
    run(scenario())


def test_sustained_flooding_is_still_banned(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a", ban_seconds=30)
            ident = os.urandom(16).hex()
            raw = await Raw(a, ident).connect()
            ping = P.encode(1, "ping", {"nonce": 1}, 1000)
            await raw.send_raw(ping * 6000)                               # far beyond burst + sustained rate
            assert await raw.closed(timeout=5.0)
            assert a.is_banned(ident)
    run(scenario())


def test_misbehavior_scores_decay_and_bans_need_recent_offences(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a", penalty_decay_seconds=0.05)            # one point forgotten every 50 ms
            a.history["someone"] = (40.0, time.monotonic())
            assert 38 <= a.score_of("someone") <= 40
            await asyncio.sleep(0.6)
            assert 26 <= a.score_of("someone") <= 30                       # ~12 points forgotten
            await asyncio.sleep(1.6)
            assert a.score_of("someone") == 0                              # fully forgiven
            assert a.score_of("never seen") == 0
            slow = Acct()
            for _ in range(3):                                             # 3 x 50 points spread far apart: no ban
                a.history["slowpoke"] = (a.score_of("slowpoke") + 50, time.monotonic())
                await asyncio.sleep(2.5)
            assert a.score_of("slowpoke") < 100 and not a.is_banned("slowpoke")
    run(scenario())


def test_two_quick_serious_offences_still_ban_immediately(tmp_path):
    """Decay must not weaken the response to real attacks: 2 x 50 within moments is 100 points."""
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")                                         # default decay: 1 point per 5 s
            ident = os.urandom(16).hex()
            for _ in range(2):
                raw = await Raw(a, ident).connect()
                await raw.send_raw(frame(b"{not json at all"))
                await raw.closed()
            assert a.is_banned(ident)
    run(scenario())


def test_honest_transaction_races_are_never_scored(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            alice, bob, poor = Acct(), Acct(), Acct()
            for _ in range(4):
                c.mine(a, alice, announce=False)
            sender = await Raw(a).connect()
            tx = alice.tx(bob, 1, nonce=1, timestamp=c.clock())
            a.chain.submit_transaction(tx)
            c.mine(a, alice, announce=False)                                # tx is now confirmed
            for i in range(15):
                await sender.send("tx", {"tx": tx})                          # a late relay of an already-mined transaction (deduplicated)
                await sender.send("tx", {"tx": poor.tx(bob, 5 + i, nonce=1, timestamp=c.clock())})   # distinct, but the sender has no funds
            await asyncio.sleep(0.5)
            peer = next(p for p in a.peers.values() if p.node_id == sender.node_id)
            assert peer.score == 0 and total(a, "p2p_penalty_points") == 0
            assert total(a, "p2p_tx_rejected_benign") >= 15                  # counted (15 unfunded + the late relay), not punished
            bad = alice.tx(bob, 1, nonce=2, timestamp=c.clock())
            bad["signature"] = "A" * 86 + "=="
            await sender.send("tx", {"tx": bad})                            # a genuinely invalid transaction still costs 20
            await asyncio.sleep(0.3)
            assert peer.score == 20
    run(scenario())


def test_transaction_announcements_are_batched_and_fully_served(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a", inv_flush_delay=0.1)
            alice, bob = Acct(), Acct()
            for _ in range(4):
                c.mine(a, alice, announce=False)
            watcher = await Raw(a).connect()
            txs = [alice.tx(bob, "0.1", nonce=n, timestamp=c.clock()) for n in range(1, 21)]
            for tx in txs:
                a.chain.submit_transaction(tx)
                a.announce_tx(tx)
            invs, end = [], time.monotonic() + 1.5
            while time.monotonic() < end and sum(len(i) for i in invs) < 20:
                try:
                    msg = await watcher.recv(0.4)
                except asyncio.TimeoutError:
                    continue
                if msg and msg[0] == "inv":
                    invs.append(msg[1]["hashes"])
            assert sum(len(i) for i in invs) == 20 and len(invs) <= 3       # 20 announcements, very few messages
            assert [h for batch in invs for h in batch] == [t["txid"] for t in txs]     # order preserved
            await watcher.send("get_data", {"kind": "tx", "hashes": [t["txid"] for t in txs]})
            got = set()
            end = time.monotonic() + 2
            while len(got) < 20 and time.monotonic() < end:
                try:
                    msg = await watcher.recv(0.5)
                except asyncio.TimeoutError:
                    continue
                if msg and msg[0] == "tx":
                    got.add(msg[1]["tx"]["txid"])
            assert got == {t["txid"] for t in txs}                            # all 20 served from ONE request (was capped at 16)
    run(scenario())


def test_block_requests_remain_capped(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            alice = Acct()
            hashes = [c.mine(a, alice, announce=False)["hash"] for _ in range(20)]
            raw = await Raw(a).connect()
            await raw.send("get_data", {"kind": "block", "hashes": hashes})
            blocks = 0
            end = time.monotonic() + 1.5
            while time.monotonic() < end:
                try:
                    msg = await raw.recv(0.4)
                except asyncio.TimeoutError:
                    break
                if msg and msg[0] == "block":
                    blocks += 1
            assert blocks == P.MAX_GET_DATA_ITEMS == 16                        # blocks stay bandwidth-limited
    run(scenario())


def test_a_transaction_burst_across_a_mesh_bans_nobody_and_reaches_every_node(tmp_path):
    """Regression for the private-testnet finding, in process: 120 transactions from 6 senders relayed across a mesh."""
    async def scenario():
        async with Cluster(tmp_path) as c:
            nodes = [await c.start("a")]
            for name in ("b", "c", "d"):
                nodes.append(await c.start(name, seeds=[addr(nodes[-1])]))
            await until(lambda: all(len(n.peers) >= 2 for n in nodes), timeout=15, msg="mesh")
            senders = [Acct() for _ in range(6)]
            for who in senders:
                for _ in range(3):
                    c.mine(nodes[0], who, announce=True)
            await until(lambda: len({n.chain.tip()["hash"] for n in nodes}) == 1 and nodes[0].chain.tip()["height"] == 18, timeout=20)
            sink = Acct()
            expected = set()
            for who in senders:
                for nonce in range(1, 21):
                    tx = who.tx(sink, "0.01", nonce=nonce, timestamp=c.clock())
                    nodes[0].chain.submit_transaction(tx)
                    nodes[0].announce_tx(tx)
                    expected.add(tx["txid"])
            await until(lambda: all({t["txid"] for t in n.chain.storage.mempool_list()} == expected for n in nodes),
                        timeout=20, msg="all 120 transactions reach every node")
            for n in nodes:
                assert total(n, "p2p_peers_banned") == 0 and total(n, "p2p_penalty_points") == 0, n.node_id
                assert len(n.peers) >= 2                                        # nobody lost their peers
    run(scenario())


def test_simultaneous_dials_leave_exactly_one_connection_not_zero(tmp_path):
    """Regression: A->B and B->A at the same instant used to be rejected as duplicates by BOTH sides, leaving no link."""
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b")
            for _ in range(5):
                tasks = [asyncio.create_task(a._connect(addr(b))), asyncio.create_task(b._connect(addr(a)))]
                await until(lambda: a.node_id in b.peers and b.node_id in a.peers, timeout=10, msg="linked")
                await asyncio.sleep(0.3)
                assert b.node_id in a.peers and a.node_id in b.peers      # the surviving connection is the same one
                assert a.peers[b.node_id].inbound != b.peers[a.node_id].inbound
                for peer in list(a.peers.values()):
                    await peer.close()
                await until(lambda: not a.peers and not b.peers, timeout=10, msg="unlinked")
                for t in tasks:
                    t.cancel()
    run(scenario())
