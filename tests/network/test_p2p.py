"""P2P networking: handshake, propagation, synchronization, recovery, abuse resistance."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from mineai.p2p import protocol as P
from tests.helpers import START, Acct, build
from tests.network.harness import Cluster, Raw, addr, frame, run, tips, until


# ====================================================================== handshake
def test_handshake_connects_peers_and_exchanges_identity(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            await until(lambda: len(a.peers) == 1 and len(b.peers) == 1)
            assert list(a.peers) == [b.node_id] and list(b.peers) == [a.node_id]
            assert next(iter(b.peers.values())).version == 1
            assert next(iter(a.peers.values())).inbound and not next(iter(b.peers.values())).inbound
    run(scenario())


def test_shared_genesis_across_nodes(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            nodes = [await c.start(n) for n in "abc"]
            genesis = {n.chain.storage.get_block_by_height(0)["hash"] for n in nodes}
            assert genesis == {c.params.genesis_hash}
    run(scenario())


@pytest.mark.parametrize("override", [
    {"network_id": "some-other-network"},
    {"genesis_hash": "ab" * 32},
    {"min_version": 2, "max_version": 5},                 # no protocol version in common
])
def test_handshake_rejects_incompatible_peers(tmp_path, override):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            raw = Raw(a)
            await raw.connect(handshake=False)
            await raw.send("hello", {**a._hello(), "node_id": os.urandom(16).hex(), "listen_port": 0, **override})
            assert await raw.closed()
            assert len(a.peers) == 0
            await raw.close()
    run(scenario())


def test_message_before_hello_is_rejected(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            raw = Raw(a)
            await raw.connect(handshake=False)
            await raw.send("ping", {"nonce": 1})
            assert await raw.closed() and len(a.peers) == 0
    run(scenario())


@pytest.mark.parametrize("garbage", [
    b"GET / HTTP/1.1\r\n\r\n", b"\x00" * 64, os.urandom(200), frame(b"not json"), frame(b'{"a":1}'),
    struct.pack(">I", 10 ** 6) if (struct := __import__("struct")) else b"",      # oversized declared length
    struct.pack(">I", 0),
])
def test_garbage_before_handshake_is_dropped(tmp_path, garbage):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            raw = Raw(a)
            await raw.connect(handshake=False)
            await raw.send_raw(garbage)
            assert await raw.closed() and len(a.peers) == 0
            b = await c.start("b", seeds=[addr(a)])                      # node is still healthy
            await until(lambda: len(a.peers) == 1)
    run(scenario())


def test_oversized_handshake_frame_rejected_without_reading_the_body(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            raw = Raw(a)
            await raw.connect(handshake=False)
            await raw.send_raw(__import__("struct").pack(">I", 5_000_000))     # header only; body never sent
            assert await raw.closed(timeout=1.0)                              # closed at once, not after a timeout
    run(scenario())


def test_silent_connection_times_out(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a", handshake_timeout=0.4)
            raw = Raw(a)
            await raw.connect(handshake=False)
            assert await raw.closed(timeout=3.0)
    run(scenario())


def test_self_connection_is_detected(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            await a._connect(addr(a))
            assert len(a.peers) == 0
    run(scenario())


def test_mixed_protocol_versions(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")                                              # speaks v1
            future = await c.start("future", seeds=[addr(a)], version_range=(2, 3))
            compatible = await c.start("compat", seeds=[addr(a)], version_range=(1, 2))
            await until(lambda: len(a.peers) == 1)
            await asyncio.sleep(0.5)
            assert list(a.peers) == [compatible.node_id]                        # v2-only peer refused
            assert next(iter(a.peers.values())).version == 1                    # highest COMMON version
            assert len(future.peers) == 0
    run(scenario())


def test_connection_limit(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a", max_peers=2)
            raws = [await Raw(a).connect() for _ in range(2)]
            extra = Raw(a)
            await extra.connect(handshake=False)
            assert await extra.closed(timeout=2.0)
            assert len(a.peers) == 2
    run(scenario())


def test_duplicate_connection_from_same_node_refused(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            same_id = os.urandom(16).hex()
            first = await Raw(a, same_id).connect()
            second = Raw(a, same_id)
            await second.connect(handshake=False)
            await second.send("hello", {**a._hello(), "node_id": same_id, "listen_port": 0})
            assert await second.closed()
            assert len(a.peers) == 1
    run(scenario())


# ====================================================================== propagation
def funded_cluster_blocks(c, node, alice, n=3):
    for _ in range(n):
        c.mine(node, alice)


def test_three_nodes_converge_on_the_same_tip(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            d = await c.start("c", seeds=[addr(b)])
            await until(lambda: all(len(n.peers) >= 1 for n in (a, b, d)))
            alice = Acct()
            for expected in range(1, 6):
                c.mine(a, alice)
                await until(lambda e=expected: all(n.chain.tip()["height"] == e for n in (a, b, d)),
                            msg=f"height {expected}")
            assert len(tips(a, b, d)) == 1
            for n in (a, b, d):
                assert n.chain.account(alice.address)["balance"] == 5 * 25_000_000
                n.chain.verify_integrity()
    run(scenario())


def test_transaction_propagation_and_confirmation_everywhere(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            d = await c.start("c", seeds=[addr(b)])
            await until(lambda: all(len(n.peers) >= 1 for n in (a, b, d)))
            alice, bob, miner = Acct(), Acct(), Acct()
            for _ in range(4):
                c.mine(a, alice)
            await until(lambda: all(n.chain.tip()["height"] == 4 for n in (a, b, d)))
            tx = alice.tx(bob, 7, nonce=1, timestamp=c.clock())
            a.chain.submit_transaction(tx)
            a.announce_tx(tx)
            await until(lambda: all(n.chain.storage.mempool_contains(tx["txid"]) for n in (b, d)), msg="tx relay")
            c.mine(d, miner)                                           # a node that never mined before confirms it
            await until(lambda: all(n.chain.tip()["height"] == 5 for n in (a, b, d)))
            for n in (a, b, d):
                assert n.chain.account(bob.address)["balance"] == 7_000_000
                assert n.chain.storage.mempool_count() == 0            # confirmed everywhere, pruned everywhere
            assert len(tips(a, b, d)) == 1
    run(scenario())


def test_invalid_transaction_is_not_relayed(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            await until(lambda: len(a.peers) == 1 and len(b.peers) == 1)
            alice, bob = Acct(), Acct()
            raw = await Raw(a).connect()
            bad = alice.tx(bob, 1)
            bad["signature"] = "A" * 86 + "=="
            await raw.send("tx", {"tx": bad})
            await asyncio.sleep(0.4)
            assert a.chain.storage.mempool_count() == 0 and b.chain.storage.mempool_count() == 0
    run(scenario())


# ====================================================================== synchronization
def test_initial_sync_of_fresh_nodes_including_multi_batch(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            alice = Acct()
            for _ in range(70):                                        # > 64: needs more than one get_blocks batch
                c.mine(a, alice, announce=False)
            b = await c.start("b", seeds=[addr(a)])
            d = await c.start("c", seeds=[addr(a)])
            await until(lambda: b.chain.tip()["height"] == 70 and d.chain.tip()["height"] == 70, timeout=20)
            assert len(tips(a, b, d)) == 1
            b.chain.verify_integrity()
            assert b.chain.minted_supply() == a.chain.minted_supply()
    run(scenario())


def test_disconnect_and_recover_with_incremental_sync(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            alice = Acct()
            for _ in range(3):
                c.mine(a, alice)
            await until(lambda: b.chain.tip()["height"] == 3)
            await c.stop(b)                                            # b goes offline
            for _ in range(6):
                c.mine(a, alice)
            b2 = await c.start("b", seeds=[addr(a)])                   # b restarts from its own database
            assert b2.chain.tip()["height"] == 3
            await until(lambda: b2.chain.tip()["height"] == 9)
            assert tips(a, b2) == {a.chain.tip()["hash"]}
    run(scenario())


def test_restart_reconnects_using_persisted_peers_only(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            await until(lambda: len(b.peers) == 1)
            assert addr(a) in b.chain.storage.peers_list()             # persisted
            alice = Acct()
            await c.stop(b)
            for _ in range(4):
                c.mine(a, alice)
            b2 = await c.start("b")                                    # NO seeds configured this time
            await until(lambda: b2.chain.tip()["height"] == 4, msg="sync via persisted peer")
    run(scenario())


def test_node_survives_being_stopped_in_the_middle_of_synchronization(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            alice = Acct()
            for _ in range(70):
                c.mine(a, alice, announce=False)
            b = await c.start("b", seeds=[addr(a)])
            await until(lambda: b.chain.tip()["height"] >= 1, timeout=10)
            await c.stop(b)                                            # cut off mid-sync (whatever height it reached)
            reached = None
            b2 = await c.start("b", seeds=[addr(a)])
            reached = b2.chain.tip()["height"]
            b2.chain.verify_integrity()                                # partial progress is consistent
            await until(lambda: b2.chain.tip()["height"] == 70, timeout=20)
            assert tips(a, b2) == {a.chain.tip()["hash"]} and reached <= 70
    run(scenario())


def test_peer_discovery_finds_nodes_not_in_seeds(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            d = await c.start("c", seeds=[addr(b)])                    # c only knows b
            await until(lambda: addr(a) in d.chain.storage.peers_list(), msg="c learns a via b")
            await until(lambda: len(d.peers) == 2, msg="c connects to a")
    run(scenario())


def test_block_ahead_of_tip_triggers_sync(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            alice = Acct()
            for _ in range(5):
                c.mine(a, alice, announce=False)
            b = await c.start("b")
            raw = await Raw(b).connect()
            tip = a.chain.tip()
            await raw.send("block", {"block": {k: tip[k] for k in P_BLOCK_FIELDS}})       # height 5 while b is at 0
            get = await raw.recv_type("get_blocks")
            assert get["locator"] == b.chain.locator() and get["locator"][-1] == c_genesis(b)
            blocks = [{k: a.chain.storage.get_block_by_height(h)[k] for k in P_BLOCK_FIELDS} for h in range(1, 6)]
            await raw.send("blocks", {"blocks": blocks})
            await until(lambda: b.chain.tip()["height"] == 5)
    run(scenario())


def c_genesis(node):
    return node.chain.storage.get_block_by_height(0)["hash"]


P_BLOCK_FIELDS = ("height", "previous_hash", "merkle_root", "timestamp", "difficulty", "nonce", "hash", "transactions")


# ====================================================================== malformed input / abuse
DEEP = b'{"v":1,"type":"tx","data":' + b"[" * 50_000 + b"]" * 50_000 + b"}"

MALFORMED = {
    "invalid json": b"{not json",
    "not an object": b"[1,2,3]",
    "missing keys": b'{"v":1,"type":"ping"}',
    "extra key": b'{"v":1,"type":"ping","data":{"nonce":1},"x":1}',
    "unknown type": b'{"v":1,"type":"shutdown","data":{}}',
    "wrong version": b'{"v":7,"type":"ping","data":{"nonce":1}}',
    "bool version": b'{"v":true,"type":"ping","data":{"nonce":1}}',
    "NaN constant": b'{"v":1,"type":"ping","data":{"nonce":NaN}}',
    "float nonce": b'{"v":1,"type":"ping","data":{"nonce":1.5}}',
    "old from_height schema": b'{"v":1,"type":"get_blocks","data":{"from_height":1,"count":5}}',
    "empty locator": b'{"v":1,"type":"get_blocks","data":{"locator":[],"count":5}}',
    "locator too long": json.dumps({"v": 1, "type": "get_blocks", "data": {"locator": ["a" * 64] * 33, "count": 5}}).encode(),
    "bad locator hash": b'{"v":1,"type":"get_blocks","data":{"locator":["xyz"],"count":5}}',
    "huge count": json.dumps({"v": 1, "type": "get_blocks", "data": {"locator": ["a" * 64], "count": 100000}}).encode(),
    "zero count": json.dumps({"v": 1, "type": "get_blocks", "data": {"locator": ["a" * 64], "count": 0}}).encode(),
    "bad hash in inv": b'{"v":1,"type":"inv","data":{"kind":"tx","hashes":["zz"]}}',
    "inv too long": json.dumps({"v": 1, "type": "inv", "data": {"kind": "tx", "hashes": ["a" * 64] * 501}}).encode(),
    "bad kind": b'{"v":1,"type":"inv","data":{"kind":"admin","hashes":["' + b"a" * 64 + b'"]}}',
    "hello after hello": json.dumps({"v": 1, "type": "hello", "data": {}}).encode(),
    "invalid utf8": b'{"v":1,"type":"ping","data":{"nonce":1}}\xff\xfe',
    "deeply nested": DEEP,
    "bad peers addr": b'{"v":1,"type":"peers","data":{"addrs":["not an address"]}}',
    "tx not object": b'{"v":1,"type":"tx","data":{"tx":5}}',
    "huge integer": b'{"v":1,"type":"ping","data":{"nonce":' + b"9" * 6000 + b"}}",
}


@pytest.mark.parametrize("name", list(MALFORMED))
def test_malformed_messages_drop_the_peer_but_not_the_node(tmp_path, name):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            good = await c.start("good", seeds=[addr(a)])
            await until(lambda: len(a.peers) == 1)
            bad = await Raw(a).connect()
            await until(lambda: len(a.peers) == 2)
            await bad.send_raw(frame(MALFORMED[name]))
            assert await bad.closed(timeout=3.0), name
            await until(lambda: len(a.peers) == 1)                     # only the offender is gone
            alice = Acct()
            c.mine(a, alice)
            await until(lambda: good.chain.tip()["height"] == 1)       # node still fully functional
    run(scenario())


def test_oversized_message_after_handshake_is_refused(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a", max_message_bytes=100_000)
            raw = await Raw(a).connect()
            await raw.send_raw(__import__("struct").pack(">I", 500_000))
            assert await raw.closed(timeout=2.0)
    run(scenario())


def test_message_flooding_gets_the_peer_banned(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a", rate_per_sec=5, rate_burst=10, ban_seconds=30)
            good = await c.start("good", seeds=[addr(a)])
            flooder_id = os.urandom(16).hex()
            raw = await Raw(a, flooder_id).connect()
            ping = P.encode(1, "ping", {"nonce": 1}, 1000)
            for _ in range(400):
                await raw.send_raw(ping)
            assert await raw.closed(timeout=3.0)
            assert a.is_banned(flooder_id)
            again = Raw(a, flooder_id)                                  # same identity cannot come back
            await again.connect(handshake=False)
            await again.send("hello", {**a._hello(), "node_id": flooder_id, "listen_port": 0})
            assert await again.closed()
            assert good.node_id in a.peers                             # honest peers unaffected
    run(scenario())


def test_misbehavior_score_survives_reconnects_and_the_ban_expires(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a", ban_seconds=0.6)
            ident = os.urandom(16).hex()
            first = await Raw(a, ident).connect()
            await first.send_raw(frame(b"{not json"))                   # +50, disconnected
            assert await first.closed()
            assert not a.is_banned(ident)                               # one strike is not a ban
            second = await Raw(a, ident).connect()                      # reconnecting does NOT reset the score
            await second.send_raw(frame(b"{not json"))                  # +50 -> 100 -> banned
            assert await second.closed()
            assert a.is_banned(ident)
            refused = Raw(a, ident)
            await refused.connect(handshake=False)
            await refused.send("hello", {**a._hello(), "node_id": ident, "listen_port": 0})
            assert await refused.closed()
            await asyncio.sleep(0.8)
            assert not a.is_banned(ident)                               # temporary, not permanent
            await Raw(a, ident).connect()
    run(scenario())


def test_repeated_invalid_blocks_lead_to_a_ban_and_change_no_state(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            await until(lambda: len(a.peers) == 1)
            alice = Acct()
            ident = os.urandom(16).hex()
            raw = await Raw(a, ident).connect()
            before = a.chain.tip()["hash"]
            for i in range(3):
                block = build(a.chain, alice, lambda blk: blk.update(previous_hash="1" * 64))
                block["hash"] = "f" * 64                                # broken hash
                await raw.send("block", {"block": block})
                await asyncio.sleep(0.05)
            assert await raw.closed(timeout=3.0)
            assert a.is_banned(ident)
            assert a.chain.tip()["hash"] == before and b.chain.tip()["hash"] == before   # nothing accepted or relayed
    run(scenario())


def test_malicious_sync_server_is_banned_and_state_stays_clean(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            src = await c.start("src")
            alice = Acct()
            for _ in range(3):
                c.mine(src, alice, announce=False)
            ident = os.urandom(16).hex()
            raw = Raw(a, ident)
            await raw.connect(height=3, total_work=str(3 * 16))          # claims 3 blocks of work (> our 1)
            get = await raw.recv_type("get_blocks")
            good = [{k: src.chain.storage.get_block_by_height(h)[k] for k in P_BLOCK_FIELDS} for h in (1, 2, 3)]
            good[1]["nonce"] += 1                                       # corrupt the second block
            await raw.send("blocks", {"blocks": good})
            assert await raw.closed(timeout=3.0)
            assert a.is_banned(ident)
            assert a.chain.tip()["height"] == 1                         # the valid first block was kept, the rest refused
            a.chain.verify_integrity()
    run(scenario())


def test_unsolicited_blocks_message_is_penalized_not_fatal(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            raw = await Raw(a).connect()
            await raw.send("blocks", {"blocks": []})
            await asyncio.sleep(0.2)
            assert not await raw.closed(timeout=0.3)
            assert next(iter(a.peers.values())).score == 20
    run(scenario())


# ====================================================================== duplicate suppression
def test_inventory_is_requested_once_and_never_again_once_known(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            alice, bob = Acct(), Acct()
            for _ in range(4):
                c.mine(a, alice, announce=False)
            raw = await Raw(a).connect()
            tx = alice.tx(bob, 1, nonce=1, timestamp=c.clock())
            inv = {"kind": "tx", "hashes": [tx["txid"]]}
            await raw.send("inv", inv)
            await raw.send("inv", inv)                                  # duplicate announcement
            first = await raw.recv_type("get_data")
            assert first["hashes"] == [tx["txid"]]
            await asyncio.sleep(0.2)
            assert a.stats["get_data_sent"] == 1                        # asked exactly once
            await raw.send("tx", {"tx": tx})
            await until(lambda: a.chain.storage.mempool_contains(tx["txid"]))
            await raw.send("inv", inv)                                  # already have it
            await asyncio.sleep(0.2)
            assert a.stats["get_data_sent"] == 1
    run(scenario())


def test_known_transactions_are_not_reprocessed_or_rebroadcast(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            watcher = await Raw(a).connect()
            alice, bob = Acct(), Acct()
            for _ in range(4):
                c.mine(a, alice, announce=False)
            sender = await Raw(a).connect()
            tx = alice.tx(bob, 1, nonce=1, timestamp=c.clock())
            await sender.send("tx", {"tx": tx})
            inv = await watcher.recv_type("inv")
            assert inv["hashes"] == [tx["txid"]]
            await sender.send("tx", {"tx": tx})                         # replay of the same transaction
            assert "inv" not in await watcher.drain(0.5)                # NOT announced a second time
            assert a.chain.storage.mempool_count() == 1
            assert next(p for p in a.peers.values() if p.node_id == sender.node_id).score == 0   # and not punished
    run(scenario())


# ====================================================================== metrics reflect what the network layer does
def test_peer_activity_and_abuse_are_visible_in_the_metrics(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            b = await c.start("b", seeds=[addr(a)])
            await until(lambda: len(a.peers) == 1 and len(b.peers) == 1)
            ma, mb = a.chain.metrics, b.chain.metrics
            assert ma.get("p2p_peers_connected", direction="inbound") == 1
            assert mb.get("p2p_peers_connected", direction="outbound") == 1
            alice = Acct()
            c.mine(a, alice)
            await until(lambda: b.chain.tip()["height"] == 1)
            assert mb.get("p2p_messages_received", type="inv") >= 1 and mb.get("p2p_messages_received", type="block") >= 1
            assert mb.get("p2p_bytes_received") > 0

            ident = os.urandom(16).hex()
            for _ in range(2):                                              # two strikes from the same identity -> ban
                raw = await Raw(a, ident).connect()
                await raw.send_raw(frame(b"{not json"))
                assert await raw.closed()
            assert ma.get("p2p_protocol_errors", reason="malformed") == 2
            assert ma.get("p2p_penalty_points") >= 100 and ma.get("p2p_peers_banned") == 1
            assert ma.get("p2p_peers_disconnected") >= 2

            for i in range(25):                                             # varied hostile garbage: series count stays bounded
                junk = Raw(a, os.urandom(16).hex())
                await junk.connect(handshake=False)
                await junk.send_raw(frame(os.urandom(5 + i)))
                await junk.closed(timeout=1.0)
            series = ma.snapshot().get("p2p_protocol_errors", {})
            assert set(series) <= {f"reason={k}" for k in ("malformed", "bad_frame", "unknown_type", "no_hello", "version",
                                                            "network", "genesis", "self", "banned", "duplicate", "table_full", "other")}
    run(scenario())


def test_sync_and_reorg_counters_over_the_network(tmp_path):
    async def scenario():
        async with Cluster(tmp_path) as c:
            a = await c.start("a")
            alice = Acct()
            for _ in range(5):
                c.mine(a, alice, announce=False)
            b = await c.start("b", seeds=[addr(a)])
            await until(lambda: b.chain.tip()["height"] == 5)
            assert b.chain.metrics.get("p2p_syncs_started") >= 1
            assert b.chain.metrics.get("blocks_received", status="extended") == 5
    run(scenario())
