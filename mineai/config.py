"""Network profiles.

Consensus parameters are frozen constants per network. They are deliberately NOT read from
environment variables: a node operator must not be able to change what is valid.
The environment only selects the profile, the data directory and API/runtime settings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

COIN_NAME = "MineAI"
TICKER = "MAI"
DECIMALS = 6
ATOMIC_UNITS = 10 ** DECIMALS

# Largest value any consensus integer may take (fits SQLite INTEGER and the u64 wire encoding).
INT_MAX = 2 ** 63 - 1


@dataclass(frozen=True)
class NetworkParams:
    name: str                   # profile name: devnet | testnet | mainnet
    network_id: str             # ASCII id bound into every signature and block hash
    address_prefix: str         # human-readable prefix; makes addresses network-specific
    label: str                  # text shown in UIs
    default_port: int
    default_p2p_port: int
    block_reward: int           # atomic units
    max_supply: int             # atomic units
    min_fee: int                # atomic units
    default_fee: int            # atomic units
    difficulty: int             # initial difficulty D0 (expected hashes per block); constant if not dynamic
    genesis_timestamp: int
    genesis_hash: str
    coinbase_maturity: int      # blocks before a coinbase output is spendable
    max_regular_txs_per_block: int = 500
    max_tx_bytes: int = 1024
    max_block_bytes: int = 512 * 1024
    mtp_window: int = 11                # median-time-past window
    max_future_seconds: int = 5 * 60
    # Difficulty adjustment (consensus, PROTOCOL.md 5.10):
    dynamic_difficulty: bool = True
    target_spacing: int = 60
    lwma_window: int = 60
    min_difficulty: int = 256
    # Mempool policy (NOT consensus):
    mempool_max_txs: int = 5000
    mempool_max_per_sender: int = 25
    mempool_timestamp_window: int = 24 * 60 * 60
    # Fork handling policy (NOT consensus; see PROTOCOL.md 8.5):
    max_reorg_depth: int = 100
    max_side_blocks: int = 2000
    max_orphans: int = 100
    enabled: bool = True
    # Optional genesis allocation: ((address, atomic_amount), ...). Empty for every public profile (no premine). Only the
    # SANDBOX profile sets it. When non-empty it is committed into the genesis block (merkle_root) and into the fingerprint.
    genesis_allocations: tuple = ()

    def __post_init__(self):
        # Overflow / sanity guards: every consensus integer must fit in INT_MAX (SQLite + u64 wire format).
        if not (0 < self.max_supply <= INT_MAX):
            raise ValueError("max_supply must be in (0, INT_MAX]")
        if not (0 < self.block_reward <= self.max_supply):
            raise ValueError("block_reward must be in (0, max_supply]")
        if not (0 < self.min_fee <= self.default_fee <= self.max_supply):
            raise ValueError("require 0 < min_fee <= default_fee <= max_supply")
        if not (1 <= self.min_difficulty <= self.difficulty <= 2 ** 62) or self.coinbase_maturity < 0 \
                or self.mtp_window < 1:
            raise ValueError("invalid difficulty / maturity / mtp_window")
        if self.target_spacing < 1 or self.lwma_window < 2 or self.max_future_seconds < 0:
            raise ValueError("invalid target_spacing / lwma_window / max_future_seconds")
        if not self.network_id.isascii() or not self.address_prefix.isalpha() or not self.address_prefix.isupper():
            raise ValueError("network_id must be ASCII and address_prefix upper-case letters")
        seen = set()
        for entry in self.genesis_allocations:
            if not (isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], str)
                    and isinstance(entry[1], int) and not isinstance(entry[1], bool) and entry[1] > 0):
                raise ValueError("genesis_allocations must be a tuple of (address, positive integer amount)")
            if not entry[0].startswith(self.address_prefix) or entry[0] in seen:
                raise ValueError("genesis allocation addresses must use the network prefix and be unique")
            seen.add(entry[0])
        if sum(a for _, a in self.genesis_allocations) > self.max_supply:
            raise ValueError("genesis allocations exceed max_supply")


def _mai(x: int) -> int:
    return x * ATOMIC_UNITS


DEVNET = NetworkParams(
    name="devnet",
    network_id="mineai-devnet-v3",
    address_prefix="DMAI",
    label="DEVNET",
    default_port=8080,
    default_p2p_port=8081,
    block_reward=_mai(25),
    max_supply=_mai(100_000_000),
    min_fee=1_000,                 # 0.001 MAI
    default_fee=10_000,            # 0.01 MAI
    difficulty=65_536,
    genesis_timestamp=1_767_225_600,   # 2026-01-01T00:00:00Z
    genesis_hash="b163bf62f484bcaf90db3f821f8e70becb702444d2f912df7680e62d3d885666",  # pinned; guarded by tests
    coinbase_maturity=3,
)

TESTNET = NetworkParams(
    name="testnet",
    network_id="mineai-testnet-v1",
    address_prefix="TMAI",
    label="TESTNET (no monetary value)",
    default_port=18080,
    default_p2p_port=18081,
    block_reward=_mai(25),
    max_supply=_mai(100_000_000),
    min_fee=1_000,
    default_fee=10_000,
    difficulty=65_536,
    genesis_timestamp=1_767_225_600,
    genesis_hash="2ddd2b532e7d313ab4fed3c7ddbba84297d9f35fd040292e2046da471d29d335",  # pinned for the public testnet release candidate (rules frozen; any change = new network id)
    coinbase_maturity=10,
)

MAINNET = NetworkParams(
    name="mainnet",
    network_id="mineai-mainnet-v1",
    address_prefix="MAI",
    label="MAINNET",
    default_port=28080,
    default_p2p_port=28081,
    block_reward=_mai(25),
    max_supply=_mai(100_000_000),
    min_fee=1_000,
    default_fee=10_000,
    difficulty=65_536,
    genesis_timestamp=0,
    genesis_hash="",
    coinbase_maturity=100,
    enabled=False,      # mainnet is intentionally disabled: placeholder for Milestone 12 planning only
)

# A network for PRIVATE multi-node testing on one machine or a lab (Milestone 7). Short target block time and low
# initial difficulty so forks, reorganizations and retargeting happen in minutes. Never for public use.
PRIVNET = NetworkParams(
    name="privnet",
    network_id="mineai-privnet-v1",
    address_prefix="PMAI",
    label="PRIVATE TEST NETWORK (no monetary value)",
    default_port=38080,
    default_p2p_port=38081,
    block_reward=_mai(25),
    max_supply=_mai(100_000_000),
    min_fee=1_000,
    default_fee=10_000,
    difficulty=16_384,
    min_difficulty=256,
    target_spacing=5,
    lwma_window=30,
    genesis_timestamp=1_767_225_600,
    genesis_hash="4b79822b6a7ebf9af6848c194b6543ef3934bc23a742a1a7e710f7c6e9c47130",
    coinbase_maturity=3,
)

# A SANDBOX for local experiments that need a large balance without mining for months (chain of its own: network id,
# SMAI addresses, ports 48080/48081). Its genesis pre-allocates coins, unlike every other profile, so it must never be
# confused with devnet/testnet/mainnet. Coins have no value and cannot move to any other network.
SANDBOX = NetworkParams(
    name="sandbox",
    network_id="mineai-sandbox-v1",
    address_prefix="SMAI",
    label="SANDBOX (pre-allocated genesis, no monetary value)",
    default_port=48080,
    default_p2p_port=48081,
    block_reward=_mai(25),
    max_supply=_mai(100_000_000),
    min_fee=1_000,
    default_fee=10_000,
    difficulty=16_384,
    min_difficulty=256,
    target_spacing=5,
    lwma_window=30,
    genesis_timestamp=1_767_225_600,
    genesis_hash="3e683e0e9dc155ccb93e53a5982c60afe8bb59d4564ab1dbca43c8aecbc0275b",
    coinbase_maturity=3,
    genesis_allocations=(("SMAIMALB5FZD3Q2542WGIC2VLQVMU7P3QQHU3KTKUJA", _mai(20_000_000)),),
)

PROFILES = {p.name: p for p in (DEVNET, TESTNET, PRIVNET, SANDBOX, MAINNET)}


def get_params(name: str | None = None) -> NetworkParams:
    name = (name or os.getenv("MINEAI_NETWORK", "devnet")).strip().lower()
    if name not in PROFILES:
        raise ValueError(f"unknown network {name!r}; choose one of: {', '.join(PROFILES)}")
    params = PROFILES[name]
    if not params.enabled:
        raise ValueError(f"the {name} profile is disabled: MineAI has no launched {name}")
    return params


# ---- runtime (non-consensus) settings -------------------------------------------------
HOST = os.getenv("MINEAI_HOST", "127.0.0.1")
PORT = int(os.getenv("MINEAI_PORT", "0")) or None       # None => profile default port
DATA_DIR = Path(os.getenv("MINEAI_DATA_DIR", str(Path.home() / ".mineai")))
MAX_BODY_BYTES = int(os.getenv("MINEAI_MAX_BODY_BYTES", "65536"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("MINEAI_RATE_LIMIT_PER_MINUTE", "240"))
LOG_LEVEL = os.getenv("MINEAI_LOG_LEVEL", "INFO").upper()

# P2P (non-consensus). Binds to loopback by default: this is a local devnet.
P2P_HOST = os.getenv("MINEAI_P2P_HOST", "127.0.0.1")
P2P_PORT = int(os.getenv("MINEAI_P2P_PORT", "0")) or None          # None => profile default
P2P_ENABLED = os.getenv("MINEAI_P2P", "1") != "0"
SEEDS = [s.strip() for s in os.getenv("MINEAI_SEEDS", "").split(",") if s.strip()]
MAX_PEERS = int(os.getenv("MINEAI_MAX_PEERS", "16"))


CONSENSUS_FIELDS = (
    "network_id", "address_prefix", "block_reward", "max_supply", "min_fee", "difficulty", "min_difficulty",
    "dynamic_difficulty", "target_spacing", "lwma_window", "genesis_timestamp", "genesis_hash", "coinbase_maturity",
    "max_regular_txs_per_block", "max_tx_bytes", "max_block_bytes", "mtp_window", "max_future_seconds",
)


def consensus_fingerprint(params: NetworkParams) -> str:
    """SHA-256 over every parameter that affects block/transaction validity. A network whose rules changed must get a
    new network id; tests pin this value for each profile so an accidental rule change cannot slip through."""
    import hashlib
    import json
    values = {name: getattr(params, name) for name in CONSENSUS_FIELDS}
    if params.genesis_allocations:                       # only when set: existing profiles keep their pinned fingerprints
        values["genesis_allocations"] = [list(e) for e in params.genesis_allocations]
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


TEST_NETWORKS = ("devnet", "testnet", "privnet", "sandbox")


def banner_lines(params: NetworkParams, version: str) -> list[str]:
    """Text every entry point shows so nobody mistakes a test network for a real one."""
    if params.name in TEST_NETWORKS:
        return [f"MineAI {version} - {params.label}",
                f"This is a TEST network ({params.name}). Its coins have NO monetary value and the network may be",
                "reset or discarded at any time without notice. Never reuse a real password for a wallet here."]
    return [f"MineAI {version} - {params.label}"]


def db_path_for(params: NetworkParams, data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / params.name / "chain.db"
