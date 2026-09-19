"""Multi-node test harness: real TCP sockets on localhost, all nodes in one event loop."""
from __future__ import annotations

import asyncio
import os
import struct
import time

from mineai.blockchain import Blockchain
from mineai.p2p import protocol as P
from mineai.p2p.node import P2PConfig, P2PNode
from tests.helpers import TEST, Clock, make_chain, mine

RAWS: list = []
FAST = dict(connect_interval=0.1, handshake_timeout=2.0, request_timeout=5.0, connect_timeout=1.0)


def run(coro):
    return asyncio.run(coro)


async def until(pred, timeout: float = 10.0, msg: str = "condition not met"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timeout: {msg}")


class Cluster:
    """Async context manager that owns nodes and always shuts them down."""

    def __init__(self, tmp_path, params=TEST):
        self.tmp, self.params, self.clock = tmp_path, params, Clock()
        self.nodes: dict[str, P2PNode] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        for raw in RAWS:
            await raw.close()
        RAWS.clear()
        for node in list(self.nodes.values()):
            await self.stop(node)

    async def start(self, name: str, seeds=(), **cfg) -> P2PNode:
        chain = make_chain(self.tmp, self.params, self.clock, name=f"{name}.db")
        node = P2PNode(chain, P2PConfig(seeds=list(seeds), **{**FAST, **cfg}))
        await node.start()
        self.nodes[name] = node
        return node

    async def stop(self, node: P2PNode) -> None:
        await node.stop()
        try:
            node.chain.close()
        except Exception:
            pass
        for k, v in list(self.nodes.items()):
            if v is node:
                del self.nodes[k]

    def mine(self, node: P2PNode, miner, announce: bool = True) -> dict:
        block = mine(node.chain, miner)
        self.clock.advance(60)
        if announce:
            node.announce_block(block)
        return block


def addr(node: P2PNode) -> str:
    return f"127.0.0.1:{node.port}"


def tips(*nodes: P2PNode):
    return {n.chain.tip()["hash"] for n in nodes}


class Raw:
    """A hand-rolled peer for sending hostile or malformed traffic to a node."""

    def __init__(self, node: P2PNode, node_id: str | None = None):
        self.node, self.node_id = node, node_id or os.urandom(16).hex()
        self.reader = self.writer = None
        RAWS.append(self)

    async def connect(self, handshake: bool = True, **hello_override):
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.node.port)
        if handshake:
            hello = {**self.node._hello(), "node_id": self.node_id, "listen_port": 0,
                     "height": 0, **hello_override}
            await self.send("hello", hello)
            await self.recv_type("hello")
        return self

    async def send(self, mtype: str, data: dict, version: int = 1):
        self.writer.write(P.encode(version, mtype, data, 10 ** 8))
        await self.writer.drain()

    async def send_raw(self, data: bytes):
        self.writer.write(data)
        try:
            await self.writer.drain()
        except ConnectionError:
            pass

    async def recv(self, timeout: float = 3.0):
        try:
            body = await asyncio.wait_for(P.read_frame(self.reader, 10 ** 8), timeout)
        except (asyncio.IncompleteReadError, ConnectionError):
            return None
        mtype, data, _ = P.decode(body, None)
        return mtype, data

    async def recv_type(self, wanted: str, timeout: float = 3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                msg = await self.recv(max(0.05, end - time.monotonic()))
            except asyncio.TimeoutError:
                continue
            if msg is None:
                raise AssertionError(f"connection closed while waiting for {wanted}")
            if msg[0] == wanted:
                return msg[1]
        raise AssertionError(f"timeout waiting for {wanted}")

    async def drain(self, duration: float = 0.4) -> list[str]:
        """Collect the types of all messages arriving during `duration` seconds."""
        seen, end = [], time.monotonic() + duration
        while time.monotonic() < end:
            try:
                msg = await self.recv(max(0.02, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
            if msg is None:
                break
            seen.append(msg[0])
        return seen

    async def closed(self, timeout: float = 3.0) -> bool:
        """True if the node closes the connection within `timeout`."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                chunk = await asyncio.wait_for(self.reader.read(65536), max(0.05, end - time.monotonic()))
            except asyncio.TimeoutError:
                continue
            except ConnectionError:
                return True
            if chunk == b"":
                return True
        return False

    async def close(self):
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


def frame(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload


async def isolate(cluster: "Cluster", node: P2PNode) -> None:
    """Take a node off the network (all P2P activity stops) but keep its chain and database open."""
    await node.stop()


async def rejoin(cluster: "Cluster", name: str, old: P2PNode, seeds) -> P2PNode:
    """Bring an isolated node's chain back online with a fresh P2P layer."""
    node = P2PNode(old.chain, P2PConfig(seeds=list(seeds), **{**FAST}))
    await node.start()
    cluster.nodes[name] = node
    return node
