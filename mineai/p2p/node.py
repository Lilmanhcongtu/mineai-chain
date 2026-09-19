"""P2P node: handshake, peer management, relay and synchronization (asyncio).

Behaviour is specified in PROTOCOL.md section 10. Consensus is never decided here: every block and
transaction is passed to Blockchain, which applies the consensus rules. This layer only decides
who to talk to, what to ask for, and how to punish misbehaviour.

Limitation (Milestone 3): the chain is linear. A block that does not extend the current tip is
ignored, so competing forks are NOT resolved yet (fork choice and reorganizations: Milestone 4).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from .. import __version__
from ..blockchain import Blockchain
from ..consensus import ValidationError
from ..log import event
from . import protocol as P

log = logging.getLogger("mineai.p2p")

# ValidationError codes that an HONEST peer can legitimately trigger through timing/races.
BENIGN_TX_CODES = {"duplicate_tx", "bad_nonce", "insufficient_funds", "mempool_full",
                   "mempool_sender_limit", "bad_timestamp"}


@dataclass
class P2PConfig:
    host: str = "127.0.0.1"
    port: int = 0                       # 0 = pick a free port (tests); the node reports the real one
    seeds: list[str] = field(default_factory=list)
    max_peers: int = 16
    max_inbound: int = 12
    target_outbound: int = 4
    handshake_timeout: float = 10.0
    idle_timeout: float = 90.0
    ping_interval: float = 30.0
    request_timeout: float = 20.0
    write_timeout: float = 20.0
    connect_interval: float = 5.0
    connect_timeout: float = 5.0
    max_message_bytes: int = 4 * 1024 * 1024
    max_handshake_bytes: int = 8 * 1024
    rate_per_sec: float = 50.0
    rate_burst: float = 100.0
    ban_threshold: int = 100
    ban_seconds: float = 600.0
    max_failures: int = 10
    max_known_peers: int = 1000
    seen_cache: int = 20_000
    version_range: tuple[int, int] = (P.SUPPORTED_MIN, P.SUPPORTED_MAX)
    user_agent: str = f"mineai/{__version__}"


class LRU:
    def __init__(self, size: int):
        self.size, self.d = size, OrderedDict()

    def add(self, key) -> bool:
        """True if the key was new."""
        if key in self.d:
            self.d.move_to_end(key)
            return False
        self.d[key] = None
        if len(self.d) > self.size:
            self.d.popitem(last=False)
        return True

    def __contains__(self, key):
        return key in self.d


class Peer:
    def __init__(self, node: "P2PNode", reader, writer, inbound: bool, ip: str, port: int):
        self.node, self.reader, self.writer, self.inbound = node, reader, writer, inbound
        self.ip, self.port = ip, port                    # socket address (port is ephemeral if inbound)
        self.node_id: str | None = None
        self.listen_port = 0
        self.height = 0
        self.tip_hash = ""
        self.total_work = 0
        self.version: int | None = None
        self.score = 0
        self.tokens = node.cfg.rate_burst
        self.refilled = time.monotonic()
        self.pending: dict[str, asyncio.Future] = {}
        self.known_tx, self.known_block = LRU(5000), LRU(5000)
        self.send_lock = asyncio.Lock()
        self.syncing = False
        self.closed = False
        self.tasks: list[asyncio.Task] = []
        self.last_message = time.monotonic()

    @property
    def advertised(self) -> str | None:
        return f"{self.ip}:{self.listen_port}" if self.listen_port else None

    async def send(self, mtype: str, data: dict) -> None:
        if self.closed:
            return
        version = self.version or P.ENVELOPE_VERSION
        frame = P.encode(version, mtype, data, self.node.cfg.max_message_bytes)
        try:
            async with self.send_lock:
                self.writer.write(frame)
                await asyncio.wait_for(self.writer.drain(), self.node.cfg.write_timeout)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            await self.close()

    async def request(self, mtype: str, data: dict, reply: str):
        if reply in self.pending:
            raise ProtocolStateError("request already outstanding")
        fut = asyncio.get_running_loop().create_future()
        self.pending[reply] = fut
        try:
            await self.send(mtype, data)
            return await asyncio.wait_for(fut, self.node.cfg.request_timeout)
        finally:
            self.pending.pop(reply, None)

    def take_token(self) -> bool:
        cfg, now = self.node.cfg, time.monotonic()
        self.tokens = min(cfg.rate_burst, self.tokens + (now - self.refilled) * cfg.rate_per_sec)
        self.refilled = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for fut in self.pending.values():
            if not fut.done():
                fut.cancel()
        try:
            self.writer.close()
            await asyncio.wait_for(self.writer.wait_closed(), 2)
        except Exception:
            pass

    def info(self) -> dict:
        return {"node_id": self.node_id, "addr": f"{self.ip}:{self.port}", "advertised": self.advertised,
                "inbound": self.inbound, "height": self.height, "total_work": str(self.total_work),
                "version": self.version, "score": self.score}


class ProtocolStateError(Exception):
    pass


class P2PNode:
    def __init__(self, chain: Blockchain, cfg: P2PConfig | None = None):
        self.chain = chain
        self.cfg = cfg or P2PConfig()
        self.node_id = os.urandom(16).hex()
        self.peers: dict[str, Peer] = {}                 # node_id -> peer (handshaken)
        self.connecting: set[str] = set()
        self.bans: dict[str, float] = {}                 # key (node_id or ip) -> monotonic expiry
        self.history: dict[str, int] = {}                # key -> accumulated misbehavior (survives reconnects)
        self._dials: set[asyncio.Task] = set()
        self.inflight: dict[tuple[str, str], float] = {}   # (kind, hash) -> when we asked; suppresses duplicate requests
        self.seen_tx, self.seen_block = LRU(self.cfg.seen_cache), LRU(self.cfg.seen_cache)
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self.loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        self._conn_tasks: set[asyncio.Task] = set()
        self.stopped = False
        self.sync_lock = asyncio.Lock()
        self.stats = {"get_data_sent": 0}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.server = await asyncio.start_server(self._on_inbound, self.cfg.host, self.cfg.port)
        self.port = self.server.sockets[0].getsockname()[1]
        for seed in self.cfg.seeds:
            if P.ADDR_RE.match(seed):
                self.chain.storage.peers_upsert(seed, int(time.time()))
        self._tasks.append(asyncio.create_task(self._connect_loop()))
        event(log, logging.INFO, "p2p_started", host=self.cfg.host, port=self.port, node_id=self.node_id)

    async def stop(self) -> None:
        self.stopped = True
        if self.server:
            self.server.close()
        for t in self._tasks:
            t.cancel()
        for peer in list(self.peers.values()):
            await peer.close()
        for t in list(self._conn_tasks) + list(self._dials):
            t.cancel()
        await asyncio.gather(*self._tasks, *self._conn_tasks, *self._dials, return_exceptions=True)
        if self.server:
            try:
                await asyncio.wait_for(self.server.wait_closed(), 2)
            except Exception:
                pass

    def peer_infos(self) -> list[dict]:
        return [p.info() for p in self.peers.values()]

    def sync_status(self) -> str:
        """no_peers | syncing | synced (best effort: based on what peers told us)."""
        if not self.peers:
            return "no_peers"
        ours = self.chain.tip_work()
        if any(p.syncing or p.total_work > ours for p in self.peers.values()):
            return "syncing"
        return "synced"

    # ------------------------------------------------------------------ bans and scoring
    def _ban_keys(self, peer: Peer) -> list[str]:
        keys = [peer.node_id] if peer.node_id else []
        if not _is_loopback(peer.ip):                     # loopback would ban every local node at once
            keys.append(peer.ip)
        return keys

    def is_banned(self, *keys: str | None) -> bool:
        now = time.monotonic()
        for k in keys:
            if k and self.bans.get(k, 0) > now:
                return True
        for k in [k for k, exp in self.bans.items() if exp <= now]:
            del self.bans[k]
        return False

    async def penalize(self, peer: Peer, points: int, reason: str) -> None:
        keys = self._ban_keys(peer)
        # The score follows the peer's identity, not the connection: reconnecting must not reset it.
        total = max([self.history.get(k, 0) for k in keys], default=peer.score) + points
        for k in keys:
            self.history[k] = total
        if len(self.history) > 10_000:
            self.history.pop(next(iter(self.history)))
        peer.score = total
        event(log, logging.INFO, "peer_penalized", peer=peer.node_id or peer.ip, points=points,
              score=peer.score, reason=reason)
        if peer.score >= self.cfg.ban_threshold:
            expiry = time.monotonic() + self.cfg.ban_seconds
            for key in keys:
                self.bans[key] = expiry
                self.history.pop(key, None)                  # a served ban wipes the slate
            event(log, logging.WARNING, "peer_banned", peer=peer.node_id or peer.ip, reason=reason)
            await peer.close()

    # ------------------------------------------------------------------ connections
    async def _on_inbound(self, reader, writer):
        ip, port = (writer.get_extra_info("peername") or ("?", 0))[:2]
        task = asyncio.current_task()
        self._conn_tasks.add(task)
        try:
            inbound = sum(1 for p in self.peers.values() if p.inbound)
            if self.stopped or self.is_banned(ip if not _is_loopback(ip) else None) \
                    or len(self.peers) >= self.cfg.max_peers or inbound >= self.cfg.max_inbound:
                writer.close()
                return
            await self._run_peer(Peer(self, reader, writer, True, ip, port))
        finally:
            self._conn_tasks.discard(task)

    async def _connect(self, addr: str) -> None:
        host, port = addr.rsplit(":", 1)
        self.connecting.add(addr)
        task = asyncio.current_task()
        self._conn_tasks.add(task)
        try:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, int(port)), self.cfg.connect_timeout)
            except (OSError, asyncio.TimeoutError):
                if addr not in self.cfg.seeds:               # configured seeds are never forgotten
                    self.chain.storage.peers_failure(addr, self.cfg.max_failures)
                return
            peer = Peer(self, reader, writer, False, host, int(port))
            peer.listen_port = int(port)
            await self._run_peer(peer, addr)
        finally:
            self.connecting.discard(addr)
            self._conn_tasks.discard(task)

    async def _connect_loop(self) -> None:
        while not self.stopped:
            try:
                outbound = sum(1 for p in self.peers.values() if not p.inbound) + len(self.connecting)
                if outbound < self.cfg.target_outbound and len(self.peers) < self.cfg.max_peers:
                    connected = {p.advertised for p in self.peers.values()}
                    for addr in self.chain.storage.peers_list():
                        if addr in self.connecting or addr in connected or self._is_self(addr):
                            continue
                        if self.is_banned(addr.rsplit(":", 1)[0]) and not _is_loopback(addr.rsplit(":", 1)[0]):
                            continue
                        task = asyncio.create_task(self._connect(addr))
                        self._dials.add(task)
                        task.add_done_callback(self._dials.discard)
                        outbound += 1
                        if outbound >= self.cfg.target_outbound:
                            break
            except Exception as exc:                       # never let the loop die
                event(log, logging.ERROR, "connect_loop_error", type=type(exc).__name__)
            await asyncio.sleep(self.cfg.connect_interval)

    def _is_self(self, addr: str) -> bool:
        host, port = addr.rsplit(":", 1)
        return int(port) == self.port and host in ("127.0.0.1", "localhost", self.cfg.host)

    # ------------------------------------------------------------------ handshake
    def _hello(self) -> dict:
        tip = self.chain.tip()
        return {"min_version": self.cfg.version_range[0], "max_version": self.cfg.version_range[1],
                "network_id": self.chain.params.network_id, "genesis_hash": self.chain.storage.get_block_by_height(0)["hash"],
                "height": tip["height"], "tip_hash": tip["hash"], "total_work": str(self.chain.tip_work()),
                "listen_port": self.port,
                "node_id": self.node_id, "user_agent": self.cfg.user_agent}

    async def _handshake(self, peer: Peer) -> None:
        cfg = self.cfg
        frame = P.encode(P.ENVELOPE_VERSION, "hello", self._hello(), cfg.max_handshake_bytes)
        peer.writer.write(frame)
        await asyncio.wait_for(peer.writer.drain(), cfg.handshake_timeout)
        body = await asyncio.wait_for(P.read_frame(peer.reader, cfg.max_handshake_bytes), cfg.handshake_timeout)
        mtype, d, _ = P.decode(body, P.ENVELOPE_VERSION)
        if mtype != "hello":
            raise P.ProtocolError("first message must be hello", penalty=50)
        mine = self._hello()
        if d["network_id"] != mine["network_id"]:
            raise P.ProtocolError("different network", penalty=100)
        if d["genesis_hash"] != mine["genesis_hash"]:
            raise P.ProtocolError("different genesis block", penalty=100)
        version = P.negotiate(cfg.version_range, d["min_version"], d["max_version"])
        if version is None:
            raise P.ProtocolError("no common protocol version", penalty=0)
        if d["node_id"] == self.node_id:
            raise P.ProtocolError("connected to self", penalty=0)
        if self.is_banned(d["node_id"], peer.ip if not _is_loopback(peer.ip) else None):
            raise P.ProtocolError("banned peer", penalty=0)
        if d["node_id"] in self.peers:
            raise P.ProtocolError("duplicate connection", penalty=0)
        if len(self.peers) >= cfg.max_peers:
            raise P.ProtocolError("peer table full", penalty=0)
        peer.node_id, peer.version = d["node_id"], version
        peer.height, peer.tip_hash, peer.total_work = d["height"], d["tip_hash"], int(d["total_work"])
        if d["listen_port"]:
            peer.listen_port = d["listen_port"]

    async def _run_peer(self, peer: Peer, dialed: str | None = None) -> None:
        cfg = self.cfg
        try:
            await self._handshake(peer)
            if peer.node_id in self.peers:               # lost a race between two simultaneous dials
                raise P.ProtocolError("duplicate connection", penalty=0)
            self.peers[peer.node_id] = peer
            if peer.advertised:
                self._remember(peer.advertised)
            event(log, logging.INFO, "peer_connected", peer=peer.node_id, inbound=peer.inbound,
                  height=peer.height, version=peer.version)
            peer.tasks.append(asyncio.create_task(self._ping_loop(peer)))
            await peer.send("get_peers", {})
            if peer.total_work > self.chain.tip_work():
                peer.tasks.append(asyncio.create_task(self._sync_from(peer)))
            await self._read_loop(peer)
        except P.ProtocolError as exc:
            event(log, logging.INFO, "peer_rejected", peer=peer.node_id or peer.ip, reason=exc.reason)
            if exc.penalty:
                await self.penalize(peer, exc.penalty, exc.reason)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:                          # a bug must not take the node down
            event(log, logging.ERROR, "peer_handler_error", type=type(exc).__name__, peer=peer.node_id)
        finally:
            for t in peer.tasks:
                t.cancel()
            if peer.node_id and self.peers.get(peer.node_id) is peer:
                del self.peers[peer.node_id]
                event(log, logging.INFO, "peer_disconnected", peer=peer.node_id)
            await peer.close()

    def _remember(self, addr: str) -> None:
        if self._is_self(addr) or self.chain.storage.peers_count() >= self.cfg.max_known_peers:
            return
        self.chain.storage.peers_upsert(addr, int(time.time()))

    # ------------------------------------------------------------------ read loop
    async def _read_loop(self, peer: Peer) -> None:
        cfg = self.cfg
        while not peer.closed and not self.stopped:
            body = await asyncio.wait_for(P.read_frame(peer.reader, cfg.max_message_bytes), cfg.idle_timeout)
            peer.last_message = time.monotonic()
            if not peer.take_token():                       # flooding: drop the message, add to score
                await self.penalize(peer, 10, "rate limit exceeded")
                continue
            try:
                mtype, data, _ = P.decode(body, peer.version)
                if mtype == "hello":
                    raise P.ProtocolError("unexpected hello", penalty=50)
                await self._dispatch(peer, mtype, data)
            except P.ProtocolError as exc:
                if exc.fatal:
                    raise
                await self.penalize(peer, exc.penalty, exc.reason)

    async def _ping_loop(self, peer: Peer) -> None:
        while not peer.closed:
            await asyncio.sleep(self.cfg.ping_interval)
            await peer.send("ping", {"nonce": int(time.time()) & 0xFFFFFFFF})

    async def _dispatch(self, peer: Peer, mtype: str, d: dict) -> None:
        if mtype in ("blocks",):
            fut = peer.pending.get("blocks")
            if fut is None or fut.done():
                raise P.ProtocolError("unsolicited blocks message", penalty=20, fatal=False)
            fut.set_result(d["blocks"])
        elif mtype == "ping":
            await peer.send("pong", d)
        elif mtype == "pong":
            pass
        elif mtype == "get_peers":
            addrs = [a for a in self.chain.storage.peers_list(P.MAX_PEERS_PER_MESSAGE) if a != peer.advertised]
            await peer.send("peers", {"addrs": addrs})
        elif mtype == "peers":
            for addr in d["addrs"]:
                self._remember(addr)
        elif mtype == "inv":
            await self._on_inv(peer, d)
        elif mtype == "get_data":
            await self._on_get_data(peer, d)
        elif mtype == "tx":
            await self._on_tx(peer, d["tx"])
        elif mtype == "block":
            await self._on_block(peer, d["block"])
        elif mtype == "get_blocks":
            await self._on_get_blocks(peer, d)

    # ------------------------------------------------------------------ inventory
    async def _on_inv(self, peer: Peer, d: dict) -> None:
        wanted, now = [], time.monotonic()
        for k in [k for k, t in self.inflight.items() if now - t > self.cfg.request_timeout]:
            del self.inflight[k]                            # an unanswered request must not block the item forever
        for h in d["hashes"]:
            if d["kind"] == "tx":
                peer.known_tx.add(h)
                if h in self.seen_tx or self.chain.storage.mempool_contains(h) or self.chain.storage.tx_location(h):
                    continue
            else:
                peer.known_block.add(h)
                if h in self.seen_block or self.chain.has_block(h):
                    continue
            if (d["kind"], h) in self.inflight:             # already asked someone for it
                continue
            self.inflight[(d["kind"], h)] = now
            wanted.append(h)
        if wanted:
            self.stats["get_data_sent"] += 1
            await peer.send("get_data", {"kind": d["kind"], "hashes": wanted})

    async def _on_get_data(self, peer: Peer, d: dict) -> None:
        for h in d["hashes"][:P.MAX_GET_DATA_ITEMS]:
            if d["kind"] == "tx":
                tx = self.chain.storage.mempool_get(h)
                if tx:
                    await peer.send("tx", {"tx": tx})
            else:
                block = self.chain.get_block_any(h)
                if block:
                    await peer.send("block", {"block": block})

    async def _on_tx(self, peer: Peer, tx: dict) -> None:
        txid = tx.get("txid") if isinstance(tx.get("txid"), str) else None
        if txid:
            self.inflight.pop(("tx", txid), None)
            peer.known_tx.add(txid)
            if txid in self.seen_tx:
                return                                      # duplicate suppression
        try:
            self.chain.submit_transaction(tx)
        except ValidationError as exc:
            if txid and exc.code in BENIGN_TX_CODES:
                self.seen_tx.add(txid)
            await self.penalize(peer, 1 if exc.code in BENIGN_TX_CODES else 20, f"tx rejected: {exc.code}")
            return
        self.seen_tx.add(tx["txid"])
        await self._broadcast("tx", tx["txid"], exclude=peer)

    async def _on_block(self, peer: Peer, block: dict) -> None:
        bh = block.get("hash") if isinstance(block.get("hash"), str) else None
        if bh:
            self.inflight.pop(("block", bh), None)
            peer.known_block.add(bh)
            if bh in self.seen_block or self.chain.has_block(bh):
                return
        before = self.chain.tip()["hash"]
        try:
            result = self.chain.process_block(block)
        except ValidationError as exc:
            if exc.code == "reorg_too_deep":                 # a chain we refuse to follow: not misbehavior
                return
            await self.penalize(peer, 5 if exc.code == "bad_timestamp" else 50, f"block rejected: {exc.code}")
            return
        if bh:
            self.seen_block.add(bh)
        if isinstance(block.get("height"), int):
            peer.height = max(peer.height, block["height"])
        if result.status == "orphan" and not peer.syncing:   # we lack its ancestry: ask for it
            peer.tasks.append(asyncio.create_task(self._sync_from(peer)))
        await self._announce_if_tip_changed(before, exclude=peer)

    async def _on_get_blocks(self, peer: Peer, d: dict) -> None:
        out, size = [], 0
        for wire in self.chain.blocks_after_locator(d["locator"], d["count"]):
            size += len(str(wire))
            if size > self.cfg.max_message_bytes // 2 and out:
                break
            out.append(wire)
        await peer.send("blocks", {"blocks": out})

    async def _announce_if_tip_changed(self, before: str, exclude: Peer | None = None) -> None:
        tip = self.chain.tip()["hash"]
        if tip != before:                                    # extension or reorganization
            self.seen_block.add(tip)
            await self._broadcast("block", tip, exclude=exclude)

    # ------------------------------------------------------------------ sync
    async def _sync_from(self, peer: Peer) -> None:
        if peer.syncing or peer.closed:
            return
        peer.syncing = True
        try:
            while not peer.closed and not self.stopped:
                try:
                    blocks = await peer.request(
                        "get_blocks", {"locator": self.chain.locator(), "count": P.MAX_BLOCKS_PER_MESSAGE}, "blocks")
                except asyncio.TimeoutError:
                    return
                if not blocks:
                    return
                before, progressed = self.chain.tip()["hash"], False
                for block in blocks:
                    try:
                        result = self.chain.process_block(block)
                    except ValidationError as exc:
                        if exc.code == "reorg_too_deep":
                            return                           # this peer is on a chain we will not follow
                        await self.penalize(peer, 5 if exc.code == "bad_timestamp" else 100,
                                            f"sync block rejected: {exc.code}")
                        return
                    if result.status != "duplicate":
                        progressed = True
                    if isinstance(block.get("hash"), str):
                        self.seen_block.add(block["hash"])
                await self._announce_if_tip_changed(before, exclude=peer)
                if not progressed or len(blocks) < P.MAX_BLOCKS_PER_MESSAGE:
                    return
        finally:
            peer.syncing = False

    # ------------------------------------------------------------------ broadcast
    async def _broadcast(self, kind: str, h: str, exclude: Peer | None = None) -> None:
        for peer in list(self.peers.values()):
            if peer is exclude or peer.closed:
                continue
            known = peer.known_tx if kind == "tx" else peer.known_block
            if h in known:
                continue
            known.add(h)
            await peer.send("inv", {"kind": kind, "hashes": [h]})

    def announce_tx(self, tx: dict) -> None:
        """Thread-safe: called by the API layer after it accepted a transaction."""
        if self.loop and not self.stopped:
            self.seen_tx_add_threadsafe(tx["txid"])
            asyncio.run_coroutine_threadsafe(self._broadcast("tx", tx["txid"]), self.loop)

    def announce_block(self, block: dict) -> None:
        if self.loop and not self.stopped:
            asyncio.run_coroutine_threadsafe(self._announce_block(block["hash"]), self.loop)

    async def _announce_block(self, h: str) -> None:
        self.seen_block.add(h)
        await self._broadcast("block", h)

    def seen_tx_add_threadsafe(self, txid: str | None) -> None:
        if txid and self.loop:
            self.loop.call_soon_threadsafe(self.seen_tx.add, txid)


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return ip == "localhost"
