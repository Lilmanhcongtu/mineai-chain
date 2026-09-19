"""Transactions that arrive before their predecessor (relay can reorder) must be held and released, not lost.

Found by the private testnet: a node that received nonce N+1 before nonce N rejected it as `bad_nonce`, remembered it as
"seen", never asked for it again and never relayed it.
"""
from __future__ import annotations

import asyncio

from tests.helpers import Acct
from tests.network.harness import Cluster, Raw, addr, run, until


async def funded_node(c, name="a", blocks=4, seeds=(), **cfg):
    node = await c.start(name, seeds=seeds, **cfg)
    alice = Acct()
    for _ in range(blocks):
        c.mine(node, alice, announce=False)
    return node, alice


def txs(c, alice, count, to=None, start=1):
    bob = to or Acct()
    return [alice.tx(bob, "0.5", nonce=n, timestamp=c.clock()) for n in range(start, start + count)]


def test_a_transaction_that_arrives_early_is_held_then_admitted_and_relayed(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a, alice = await funded_node(c)
            watcher = await Raw(a).connect()
            sender = await Raw(a).connect()
            t1, t2 = txs(c, alice, 2)
            await sender.send("tx", {"tx": t2})                          # nonce 2 first: its predecessor is missing
            await asyncio.sleep(0.3)
            assert a.chain.storage.mempool_count() == 0 and t2["txid"] in a.future_txs
            assert "inv" not in await watcher.drain(0.2)                  # not relayed while it cannot be valid
            await sender.send("tx", {"tx": t1})                          # the predecessor arrives
            await until(lambda: a.chain.storage.mempool_count() == 2, msg="both admitted")
            assert not a.future_txs
            announced = []
            end = asyncio.get_running_loop().time() + 1.0
            while len(announced) < 2 and asyncio.get_running_loop().time() < end:
                try:
                    msg = await watcher.recv(0.5)
                except asyncio.TimeoutError:
                    break
                if msg and msg[0] == "inv":
                    announced += msg[1]["hashes"]
            assert set(announced) == {t1["txid"], t2["txid"]} and announced == [t1["txid"], t2["txid"]]   # relayed in order
            assert [t["nonce"] for t in a.chain.storage.mempool_for_sender(alice.address)] == [1, 2]
            assert next(p for p in a.peers.values() if p.node_id == sender.node_id).score == 0            # nobody was punished
    run(scenario())


def test_a_whole_burst_delivered_in_reverse_order_is_fully_recovered(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a, alice = await funded_node(c)
            sender = await Raw(a).connect()
            burst = txs(c, alice, 6)
            for tx in reversed(burst):                                    # 6,5,4,3,2,1
                await sender.send("tx", {"tx": tx})
            await until(lambda: a.chain.storage.mempool_count() == 6, msg="all six admitted once nonce 1 arrived")
            assert [t["nonce"] for t in a.chain.storage.mempool_for_sender(alice.address)] == [1, 2, 3, 4, 5, 6]
            assert not a.future_txs
            assert a.chain.metrics.get("p2p_future_txs_parked") == 5 and a.chain.metrics.get("p2p_future_txs_released") == 5
    run(scenario())


def test_early_transactions_propagate_across_the_network_once_released(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a, alice = await funded_node(c)
            b = await c.start("b", seeds=[addr(a)])
            d = await c.start("c", seeds=[addr(b)])
            await until(lambda: all(len(n.peers) >= 1 for n in (a, b, d)))
            await until(lambda: b.chain.tip()["height"] == 4 and d.chain.tip()["height"] == 4)
            sender = await Raw(a).connect()
            burst = txs(c, alice, 5)
            for tx in reversed(burst):
                await sender.send("tx", {"tx": tx})
            want = {t["txid"] for t in burst}
            await until(lambda: all({t["txid"] for t in n.chain.storage.mempool_list()} == want for n in (a, b, d)),
                        timeout=10, msg="all five transactions reach every node")
    run(scenario())


def test_the_holding_pool_is_bounded_per_sender_and_globally(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a, alice = await funded_node(c, future_tx_per_sender=4, future_tx_max=6)
            sender = await Raw(a).connect()
            for tx in txs(c, alice, 12, start=2):                         # nonce 1 never arrives
                await sender.send("tx", {"tx": tx})
            await asyncio.sleep(0.4)
            assert len(a.future_txs) == 4                                 # per-sender cap
            others = [Acct() for _ in range(8)]
            for who in others:
                for tx in txs(c, who, 1, start=2):                        # each: valid signature, missing predecessor
                    await sender.send("tx", {"tx": tx})
            await asyncio.sleep(0.4)
            assert len(a.future_txs) <= 6                                 # global cap
            assert a.chain.storage.mempool_count() == 0
    run(scenario())


def test_parked_transactions_expire(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a, alice = await funded_node(c, future_tx_ttl=0.4)
            sender = await Raw(a).connect()
            first = txs(c, alice, 1, start=2)[0]
            await sender.send("tx", {"tx": first})
            await asyncio.sleep(0.2)
            assert first["txid"] in a.future_txs
            await asyncio.sleep(0.6)
            other = Acct()
            await sender.send("tx", {"tx": txs(c, other, 1, start=2)[0]})      # parking anything prunes the expired entries
            await asyncio.sleep(0.3)
            assert first["txid"] not in a.future_txs
    run(scenario())


def test_only_genuine_future_nonces_are_parked(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a, alice = await funded_node(c)
            sender = await Raw(a).connect()
            t1, t2 = txs(c, alice, 2)
            await sender.send("tx", {"tx": t1})
            await until(lambda: a.chain.storage.mempool_count() == 1)
            replay = alice.tx(Acct(), 1, nonce=1, timestamp=c.clock() + 1)      # a DIFFERENT tx reusing nonce 1
            await sender.send("tx", {"tx": replay})
            await asyncio.sleep(0.3)
            assert not a.future_txs                                       # a conflicting nonce is not a "future" one
            bad = txs(c, alice, 1, start=3)[0]
            bad["signature"] = "A" * 86 + "=="                             # invalid signature: rejected before any nonce logic
            before = next(iter(a.peers.values())).score
            await sender.send("tx", {"tx": bad})
            await asyncio.sleep(0.3)
            assert not a.future_txs and next(p for p in a.peers.values() if p.node_id == sender.node_id).score >= 20
    run(scenario())


def test_a_parked_transaction_that_became_invalid_is_dropped_not_admitted(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a, alice = await funded_node(c)
            sender = await Raw(a).connect()
            t1, t2 = txs(c, alice, 2)
            await sender.send("tx", {"tx": t2})
            await asyncio.sleep(0.3)
            assert t2["txid"] in a.future_txs
            a.chain.submit_transaction(t1)                                # nonce 1 arrives by another route (the API)...
            a.chain.submit_transaction(alice.tx(Acct(), 3, nonce=2, timestamp=c.clock() + 5))   # ...and so does a DIFFERENT nonce 2
            await a._release_future_txs()
            assert not a.future_txs and not a.chain.storage.mempool_contains(t2["txid"])
            assert a.chain.storage.mempool_count() == 2
    run(scenario())


def test_local_api_transactions_release_parked_ones(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a, alice = await funded_node(c)
            sender = await Raw(a).connect()
            t1, t2 = txs(c, alice, 2)
            await sender.send("tx", {"tx": t2})
            await asyncio.sleep(0.3)
            a.chain.submit_transaction(t1)                                # e.g. submitted through this node's own API
            a.announce_tx(t1)                                             # ...which announces it and retries parked ones
            await until(lambda: a.chain.storage.mempool_count() == 2, msg="t2 released by the local submission")
    run(scenario())
