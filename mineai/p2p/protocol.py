"""P2P wire format: length-prefixed JSON frames with strict, allow-list validation.

Everything here is normative and documented in PROTOCOL.md section 10. Nothing in a received
message is trusted: sizes are checked before bodies are read, JSON is parsed strictly, and every
field of every message type is validated before any handler sees it.
"""
from __future__ import annotations

import asyncio
import json
import re
import struct

ENVELOPE_VERSION = 1                 # version of the frame/handshake format itself
SUPPORTED_MIN, SUPPORTED_MAX = 1, 1  # protocol versions this software speaks
MAX_INV = 500
MAX_PEERS_PER_MESSAGE = 50
MAX_BLOCKS_PER_MESSAGE = 64
MAX_GET_DATA_ITEMS = 16              # items served per get_data (bounds outbound bandwidth)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ADDR_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}:[0-9]{1,5}$")
NODE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
AGENT_RE = re.compile(r"^[A-Za-z0-9._/\- ]{0,64}$")
NETWORK_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


class ProtocolError(Exception):
    """The remote peer violated the protocol. `penalty` is added to its misbehavior score."""

    def __init__(self, reason: str, penalty: int = 20, fatal: bool = True):
        super().__init__(reason)
        self.reason, self.penalty, self.fatal = reason, penalty, fatal


# ------------------------------------------------------------------ primitive validators
def _int(v, name, lo=0, hi=2 ** 63 - 1) -> int:
    if type(v) is not int or not lo <= v <= hi:
        raise ProtocolError(f"bad {name}")
    return v


def _hash(v, name) -> str:
    if not isinstance(v, str) or not HASH_RE.match(v):
        raise ProtocolError(f"bad {name}")
    return v


def _keys(d, expected: set, what: str) -> dict:
    if not isinstance(d, dict) or set(d) != expected:
        raise ProtocolError(f"{what}: unexpected or missing fields")
    return d


def _hash_list(v, name, limit) -> list:
    if not isinstance(v, list) or not 1 <= len(v) <= limit:
        raise ProtocolError(f"bad {name} list")
    for h in v:
        _hash(h, name)
    return v


# ------------------------------------------------------------------ per-message schemas
def _hello(d):
    _keys(d, {"min_version", "max_version", "network_id", "genesis_hash", "height", "tip_hash",
              "listen_port", "node_id", "user_agent"}, "hello")
    lo, hi = _int(d["min_version"], "min_version", 1, 1000), _int(d["max_version"], "max_version", 1, 1000)
    if lo > hi:
        raise ProtocolError("bad version range")
    if not isinstance(d["network_id"], str) or not NETWORK_ID_RE.match(d["network_id"]):
        raise ProtocolError("bad network_id")
    _hash(d["genesis_hash"], "genesis_hash")
    _int(d["height"], "height")
    _hash(d["tip_hash"], "tip_hash")
    _int(d["listen_port"], "listen_port", 0, 65535)
    if not isinstance(d["node_id"], str) or not NODE_ID_RE.match(d["node_id"]):
        raise ProtocolError("bad node_id")
    if not isinstance(d["user_agent"], str) or not AGENT_RE.match(d["user_agent"]):
        raise ProtocolError("bad user_agent")
    return d


def _nonce(d):
    _keys(d, {"nonce"}, "ping/pong")
    _int(d["nonce"], "nonce", 0, 2 ** 32 - 1)
    return d


def _empty(d):
    _keys(d, set(), "empty message")
    return d


def _peers(d):
    _keys(d, {"addrs"}, "peers")
    if not isinstance(d["addrs"], list) or len(d["addrs"]) > MAX_PEERS_PER_MESSAGE:
        raise ProtocolError("bad addrs list")
    for a in d["addrs"]:
        if not isinstance(a, str) or not ADDR_RE.match(a) or not 1 <= int(a.rsplit(":", 1)[1]) <= 65535:
            raise ProtocolError("bad peer address")
    return d


def _inv(d):
    _keys(d, {"kind", "hashes"}, "inv/get_data")
    if d["kind"] not in ("tx", "block"):
        raise ProtocolError("bad kind")
    _hash_list(d["hashes"], "hash", MAX_INV)
    return d


def _tx(d):
    _keys(d, {"tx"}, "tx")
    if not isinstance(d["tx"], dict):
        raise ProtocolError("bad tx")
    return d


def _block(d):
    _keys(d, {"block"}, "block")
    if not isinstance(d["block"], dict):
        raise ProtocolError("bad block")
    return d


def _get_blocks(d):
    _keys(d, {"from_height", "count"}, "get_blocks")
    _int(d["from_height"], "from_height", 1, 2 ** 31)
    _int(d["count"], "count", 1, MAX_BLOCKS_PER_MESSAGE)
    return d


def _blocks(d):
    _keys(d, {"blocks"}, "blocks")
    if not isinstance(d["blocks"], list) or len(d["blocks"]) > MAX_BLOCKS_PER_MESSAGE or \
            not all(isinstance(b, dict) for b in d["blocks"]):
        raise ProtocolError("bad blocks list")
    return d


SCHEMAS = {
    "hello": _hello, "ping": _nonce, "pong": _nonce, "get_peers": _empty, "peers": _peers,
    "inv": _inv, "get_data": _inv, "tx": _tx, "block": _block,
    "get_blocks": _get_blocks, "blocks": _blocks,
}


# ------------------------------------------------------------------ framing
def encode(version: int, mtype: str, data: dict, max_bytes: int) -> bytes:
    body = json.dumps({"v": version, "type": mtype, "data": data},
                      separators=(",", ":"), allow_nan=False, sort_keys=True).encode("utf-8")
    if len(body) > max_bytes:
        raise ValueError("message exceeds the maximum size")
    return struct.pack(">I", len(body)) + body


async def read_frame(reader: asyncio.StreamReader, max_bytes: int) -> bytes:
    """Read one frame. The declared length is checked BEFORE any body byte is read."""
    header = await reader.readexactly(4)
    (length,) = struct.unpack(">I", header)
    if length == 0 or length > max_bytes:
        raise ProtocolError(f"frame length {length} outside 1..{max_bytes}", penalty=100)
    return await reader.readexactly(length)


def _no_constants(name):
    raise ValueError(f"illegal JSON constant {name}")


def decode(body: bytes, expected_version: int | None) -> tuple[str, dict, int]:
    """Strictly parse a frame body -> (type, validated data, version)."""
    try:
        msg = json.loads(body.decode("utf-8"), parse_constant=_no_constants)
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        raise ProtocolError("malformed JSON", penalty=50)
    if not isinstance(msg, dict) or set(msg) != {"v", "type", "data"}:
        raise ProtocolError("bad envelope", penalty=50)
    version, mtype, data = msg["v"], msg["type"], msg["data"]
    if type(version) is not int or (expected_version is not None and version != expected_version):
        raise ProtocolError("bad protocol version in envelope", penalty=50)
    if not isinstance(mtype, str) or mtype not in SCHEMAS:
        raise ProtocolError("unknown message type", penalty=20)
    return mtype, SCHEMAS[mtype](data), version


def negotiate(local: tuple[int, int], remote_min: int, remote_max: int) -> int | None:
    """Highest protocol version both sides support, or None."""
    best = min(local[1], remote_max)
    return best if best >= max(local[0], remote_min) else None
