#!/usr/bin/env python
"""Private testnet harness (Milestone 7): five real node processes, real miners, real failures.

    .venv\\Scripts\\python.exe scripts\\private_testnet.py run [--quick] [--only a,b] [--out docs\\reports]
    .venv\\Scripts\\python.exe scripts\\private_testnet.py monitor [--base-port 38100] [--nodes 5]

Every node is a separate `python -m mineai node` process on the `privnet` profile (5-second target block time,
low initial difficulty). The harness kills them with TerminateProcess (no graceful shutdown), corrupts their
databases, isolates and rejoins them, floods them with transactions, and attacks their P2P port, while a poller
records every node's height, tip, peers, mempool, difficulty and counters once per second.

Keys used for mining and transaction tests are generated in memory only and never written anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx                                            # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey   # noqa: E402

from mineai import config                                # noqa: E402
from mineai.crypto import address_from_public_key, public_key_bytes, sign_transaction   # noqa: E402
from mineai.p2p import protocol as P                     # noqa: E402

PARAMS = config.get_params("privnet")
PY = sys.executable
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
API_BASE, P2P_BASE = 38100, 38200


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def kill_tree(proc: subprocess.Popen | None) -> None:
    """Hard-kill a process and everything it spawned (miner worker processes would otherwise be orphaned)."""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except OSError:
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def wait_until(pred, timeout: float, what: str = "", every: float = 0.5) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(every)
    return False


class Account:
    """An in-memory keypair. Never persisted."""

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.address = address_from_public_key(public_key_bytes(self.key), PARAMS.address_prefix)
        self.nonce_used = 0

    def transfer(self, to: str, amount: int, fee: int, nonce: int) -> dict:
        return sign_transaction(self.key, {"recipient": to, "amount": amount, "fee": fee, "nonce": nonce,
                                           "timestamp": int(time.time())}, PARAMS.network_id, PARAMS.address_prefix)


# ====================================================================== processes
class NetNode:
    def __init__(self, name: str, index: int, workdir: Path):
        self.name, self.index = name, index
        self.api_port, self.p2p_port = API_BASE + index, P2P_BASE + index
        self.data_dir = workdir / name
        self.log_path = workdir / f"{name}.log"
        self.proc: subprocess.Popen | None = None
        self.starts = 0
        self.http = httpx.Client(timeout=2.0)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    def env(self, seeds: list[str], p2p: bool) -> dict:
        e = dict(os.environ)
        e.update(MINEAI_NETWORK="privnet", MINEAI_DATA_DIR=str(self.data_dir), MINEAI_PORT=str(self.api_port),
                 MINEAI_P2P_PORT=str(self.p2p_port), MINEAI_SEEDS=",".join(seeds), MINEAI_P2P="1" if p2p else "0",
                 MINEAI_HOST="127.0.0.1", MINEAI_P2P_HOST="127.0.0.1", MINEAI_RATE_LIMIT_PER_MINUTE="1000000",
                 MINEAI_LOG_LEVEL="INFO", PYTHONUNBUFFERED="1")
        e.pop("MINEAI_WALLET_PASSWORD", None)
        return e

    def start(self, seeds: list[str], p2p: bool = True) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.starts += 1
        with open(self.log_path, "ab") as fh:
            fh.write(f"\n=== start #{self.starts} p2p={p2p} seeds={seeds} at {time.time():.0f} ===\n".encode())
        self.proc = subprocess.Popen([PY, "-m", "mineai", "node"], env=self.env(seeds, p2p), cwd=ROOT,
                                     stdout=subprocess.DEVNULL, stderr=open(self.log_path, "ab"),
                                     creationflags=NO_WINDOW)

    def kill(self) -> None:
        kill_tree(self.proc)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def get(self, path: str):
        try:
            r = self.http.get(self.url + path)
            return r.json() if r.status_code < 400 else None
        except Exception:
            return None

    def status(self) -> dict | None:
        return self.get("/api/v1/status")

    def metrics(self) -> dict | None:
        return self.get("/api/v1/metrics")

    def wait_ready(self, timeout: float = 45) -> bool:
        return wait_until(lambda: (self.get("/api/v1/health") or {}).get("status") == "ok", timeout, every=0.3)

    def tail_log(self, lines: int = 12) -> str:
        try:
            return "".join(self.log_path.read_text(errors="replace").splitlines(True)[-lines:])
        except OSError:
            return ""

    def check_db(self) -> tuple[bool, str]:
        """`mineai check` on the (stopped) node's database."""
        r = subprocess.run([PY, "-m", "mineai", "check", "--network", "privnet", "--data-dir", str(self.data_dir)],
                           capture_output=True, text=True, cwd=ROOT, creationflags=NO_WINDOW, timeout=300)
        return r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout + r.stderr).strip() else ""


class MinerProc:
    def __init__(self, node: NetNode, account: Account, threads: int = 1):
        self.node, self.account = node, account
        self.proc = subprocess.Popen(
            [PY, "-m", "mineai", "miner", "--network", "privnet", "--address", account.address, "--node", node.url,
             "--blocks", "1000000", "--threads", str(threads)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)

    def stop(self) -> None:
        kill_tree(self.proc)


# ====================================================================== harness
@dataclass
class Result:
    scenario: str
    name: str
    passed: bool
    detail: str


@dataclass
class Harness:
    workdir: Path
    quick: bool
    nodes: list[NetNode] = field(default_factory=list)
    miners: list[MinerProc] = field(default_factory=list)
    results: list[Result] = field(default_factory=list)
    timeline: list[tuple[float, str]] = field(default_factory=list)
    snapshots: list[dict] = field(default_factory=list)
    scenario_times: dict[str, float] = field(default_factory=dict)
    notes: dict = field(default_factory=dict)
    current: str = ""
    t0: float = field(default_factory=time.time)
    _stop_poll: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---------------------------------------------------------------- helpers
    def scale(self, seconds: float) -> float:
        return seconds / 3 if self.quick else seconds

    def event(self, text: str) -> None:
        self.timeline.append((time.time() - self.t0, f"[{self.current}] {text}"))
        log(f"{self.current}: {text}")

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.results.append(Result(self.current, name, bool(condition), detail))
        log(f"  {'PASS' if condition else 'FAIL'}  {name}  {detail}")
        return bool(condition)

    def seeds_for(self, node: NetNode) -> list[str]:
        """A chain of seeds (node i seeds node i-1): discovery must turn this into a mesh."""
        return [f"127.0.0.1:{self.nodes[node.index - 1].p2p_port}"] if node.index > 0 else []

    def live(self) -> list[NetNode]:
        return [n for n in self.nodes if n.alive]

    def statuses(self) -> dict[str, dict | None]:
        return {n.name: n.status() if n.alive else None for n in self.nodes}

    def converged(self, nodes: list[NetNode] | None = None) -> bool:
        nodes = nodes or self.live()
        sts = [n.status() for n in nodes]
        if not sts or any(s is None for s in sts):
            return False
        return len({(s["height"], s["latest_hash"]) for s in sts}) == 1

    def start_miner(self, node: NetNode, account: Account, threads: int = 1) -> MinerProc:
        m = MinerProc(node, account, threads)
        self.miners.append(m)
        return m

    def stop_miners(self) -> None:
        for m in self.miners:
            m.stop()
        self.miners.clear()

    def quiesce(self, timeout: float = 45) -> bool:
        self.stop_miners()
        return wait_until(self.converged, timeout, every=0.5)

    def height(self) -> int:
        return max((s["height"] for s in self.statuses().values() if s), default=0)

    # ---------------------------------------------------------------- monitoring
    def poller(self) -> None:
        while not self._stop_poll.wait(1.0):
            row = {"t": round(time.time() - self.t0, 1), "scenario": self.current, "nodes": {}}
            for n in self.nodes:
                s = n.status() if n.alive else None
                row["nodes"][n.name] = None if s is None else {
                    "height": s["height"], "tip": s["latest_hash"][:12], "peers": s["peer_count"],
                    "mempool": s["mempool_size"], "difficulty": s["difficulty"], "reorgs": s["reorgs_since_start"],
                    "work": s["total_work"], "sync": s["sync_status"]}
            with self._lock:
                self.snapshots.append(row)

    def block_intervals(self, node: NetNode, first: int, last: int) -> list[int]:
        """Seconds between consecutive blocks first..last as recorded in the chain."""
        stamps: dict[int, int] = {}
        offset = 0
        top = (node.status() or {}).get("height", 0)
        while True:
            page = node.get(f"/api/v1/blocks?limit=100&offset={offset}")
            if not page:
                break
            for b in page:
                stamps[b["height"]] = b["timestamp"]
            offset += 100
            if offset > top or min(stamps) <= first:
                break
        return [stamps[h] - stamps[h - 1] for h in range(max(first, 1) + 1, last + 1) if h in stamps and h - 1 in stamps]

    def difficulties(self, node: NetNode, first: int, last: int) -> list[int]:
        out, offset = {}, 0
        top = (node.status() or {}).get("height", 0)
        while True:
            page = node.get(f"/api/v1/blocks?limit=100&offset={offset}")
            if not page:
                break
            for b in page:
                out[b["height"]] = b["difficulty"]
            offset += 100
            if offset > top or min(out) <= first:
                break
        return [out[h] for h in range(first, last + 1) if h in out]

    # ---------------------------------------------------------------- scenarios
    def run_scenario(self, name: str, fn) -> None:
        self.current = name
        start = time.time()
        self.event("begin")
        try:
            fn()
        except Exception as exc:                                   # a harness/scenario crash is a failure, not a pass
            import traceback
            self.check("scenario completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        self.scenario_times[name] = time.time() - start
        self.event(f"end ({self.scenario_times[name]:.0f}s)")

    def s_setup(self) -> None:
        self.nodes = [NetNode(f"n{i + 1}", i, self.workdir) for i in range(5)]
        self.accounts = {"A": Account(), "B": Account()}
        for n in self.nodes:
            n.start(self.seeds_for(n))
        ready = [n.wait_ready() for n in self.nodes]
        self.check("all 5 nodes start and answer the health check", all(ready), f"{sum(ready)}/5 ready")
        genesis = {n.status()["genesis_hash"] for n in self.nodes if n.alive and n.status()}
        self.check("all nodes share the pinned privnet genesis", genesis == {PARAMS.genesis_hash}, f"{len(genesis)} distinct")
        ok = wait_until(lambda: all((n.status() or {}).get("peer_count", 0) >= 3 for n in self.nodes), self.scale(90))
        peers = {n.name: (n.status() or {}).get("peer_count") for n in self.nodes}
        self.check("seeded as a chain, discovery forms a mesh (every node has >= 3 peers)", ok, str(peers))

    def s_baseline(self) -> None:
        a, b = self.accounts["A"], self.accounts["B"]
        self.start_miner(self.nodes[0], a)
        self.start_miner(self.nodes[2], b)
        self.event("2 miners started on n1 and n3")
        time.sleep(self.scale(75))
        h_before = self.height()
        snaps = [s for s in self.statuses().values() if s]
        quiet = self.quiesce()
        h = self.height()
        self.check("chain grows under two competing miners", h >= 10, f"height {h}")
        self.check("after mining stops, all 5 nodes agree on the same tip", quiet, f"height {h}")
        first = min(6, h)
        iv = self.block_intervals(self.nodes[0], first, h)
        if iv:
            self.notes["baseline_interval_mean"] = round(statistics.mean(iv), 2)
            self.notes["baseline_interval_median"] = statistics.median(iv)
            self.event(f"block interval mean {statistics.mean(iv):.1f}s median {statistics.median(iv):.1f}s over {len(iv)} blocks")
        reorgs = sum((n.metrics() or {}).get("counters", {}).get("reorgs", {}).get("", 0) for n in self.live())
        self.notes["baseline_reorgs"] = reorgs
        self.event(f"reorganizations observed across nodes so far: {reorgs}")

    def s_kill_recovery(self) -> None:
        rng = random.Random(7)
        victims = [self.nodes[3], self.nodes[1], self.nodes[4]]
        self.start_miner(self.nodes[0], self.accounts["A"])
        self.start_miner(self.nodes[2], self.accounts["B"])
        for i, victim in enumerate(victims, start=1):
            time.sleep(self.scale(rng.uniform(4, 9)))                 # kill at a random moment while blocks are flowing
            h_at_kill = self.height()
            victim.kill()
            self.event(f"HARD-KILLED {victim.name} at network height {h_at_kill} (no graceful shutdown)")
            ok, msg = victim.check_db()
            self.check(f"kill #{i}: {victim.name}'s database is consistent right after the crash", ok, msg)
            time.sleep(self.scale(rng.uniform(10, 20)))               # the network keeps going without it
            victim.start(self.seeds_for(victim))
            up = victim.wait_ready()
            self.check(f"kill #{i}: {victim.name} restarts", up)
            caught = wait_until(lambda v=victim: (v.status() or {}).get("height", 0) >= self.height() - 2, self.scale(90))
            self.check(f"kill #{i}: {victim.name} catches up to the live network (within 2 blocks)", caught,
                       f"{(victim.status() or {}).get('height')} vs {self.height()}")
        quiet = self.quiesce()
        self.check("after 3 crashes and restarts all nodes converge on one tip", quiet, f"height {self.height()}")

    def s_rolling_restart(self) -> None:
        self.start_miner(self.nodes[0], self.accounts["A"])
        self.start_miner(self.nodes[3], self.accounts["B"])
        for n in self.nodes[1:]:
            time.sleep(self.scale(3))
            n.kill()
            n.start(self.seeds_for(n))
            n.wait_ready()
            self.event(f"restarted {n.name}")
        time.sleep(self.scale(10))
        quiet = self.quiesce(60)
        self.check("after a rolling restart of 4 nodes under load, all nodes converge", quiet, f"height {self.height()}")
        peers = {n.name: (n.status() or {}).get("peer_count") for n in self.live()}
        self.check("the mesh re-forms from persisted peers (>= 2 peers each)", all((v or 0) >= 2 for v in peers.values()), str(peers))

    def s_partition(self) -> None:
        group_a, group_b = [self.nodes[0]], self.nodes[2:]            # n1 isolated; n2 also isolated (idle); n3-n5 together
        idle = self.nodes[1]
        self.quiesce()
        base = self.height()
        for n in (self.nodes[0], idle):
            n.kill()
            n.start([], p2p=False)                                     # no P2P at all: a hard partition
            n.wait_ready()
        self.event(f"n1 and n2 isolated at height {base}")
        self.start_miner(self.nodes[0], self.accounts["A"], threads=2)  # the isolated node mines alone (more hashrate)
        self.start_miner(self.nodes[3], self.accounts["B"])            # the majority side mines with 1 thread
        time.sleep(self.scale(45))
        heights = {n.name: (n.status() or {}).get("height") for n in self.live()}
        tips = {n.name: (n.status() or {}).get("latest_hash", "")[:10] for n in self.live()}
        self.check("while partitioned the two sides really diverge", len({tips["n1"], tips["n3"]}) == 2, f"heights {heights}")
        self.stop_miners()
        work = {n.name: int((n.status() or {}).get("total_work", 0)) for n in self.live()}
        heavy_is_n1 = work["n1"] > work["n3"]
        self.notes["partition_work_n1_vs_majority"] = (work["n1"], work["n3"])
        self.event(f"before healing: n1 work {work['n1']:,} vs majority work {work['n3']:,} -> the {'isolated node' if heavy_is_n1 else 'majority side'} is heavier")
        reorgs_before = {n.name: (n.metrics() or {}).get("counters", {}).get("reorgs", {}).get("", 0) for n in self.live()}
        for n in (self.nodes[0], idle):
            n.kill()
            n.start(self.seeds_for(n) if n is not self.nodes[0] else [f"127.0.0.1:{self.nodes[2].p2p_port}"])
            n.wait_ready()
        self.event("partition healed: n1 and n2 rejoined with P2P")
        merged = wait_until(self.converged, self.scale(90))
        self.check("after healing, all 5 nodes converge on one chain", merged, f"height {self.height()}")
        reorgs_after = {n.name: (n.metrics() or {}).get("counters", {}).get("reorgs", {}).get("", 0) for n in self.live()}
        light = ["n3", "n4", "n5"] if heavy_is_n1 else ["n1"]
        heavy = ["n1"] if heavy_is_n1 else ["n3", "n4", "n5"]
        gained = {k: reorgs_after[k] - (reorgs_before.get(k) or 0) for k in reorgs_after}
        self.check("every node on the LIGHTER side reorganized (and the heavier side did not)",
                   all(gained[k] >= 1 for k in light) and all(gained[k] == 0 for k in heavy),
                   f"reorgs gained {gained}; lighter side {light}")
        final_work = int((self.nodes[0].status() or {}).get("total_work", 0))
        self.check("the surviving chain is the one with the most cumulative work", final_work >= max(work["n1"], work["n3"]),
                   f"final {final_work:,} vs max side {max(work['n1'], work['n3']):,}")
        winner = (self.nodes[0].status() or {}).get("total_work")
        self.event(f"final total work {winner}")

    def s_late_joiner(self) -> None:
        node = self.nodes[4]
        self.start_miner(self.nodes[0], self.accounts["A"])
        time.sleep(self.scale(10))
        node.kill()
        shutil.rmtree(node.data_dir, ignore_errors=True)               # a brand-new node: no data at all
        h = self.height()
        self.event(f"wiped {node.name}'s data directory (network height {h}); starting it from nothing")
        t = time.time()
        node.start(self.seeds_for(node))
        node.wait_ready()
        caught = wait_until(lambda: (node.status() or {}).get("height", 0) >= self.height() - 2, self.scale(120))
        took = time.time() - t
        self.notes["initial_sync_seconds"] = round(took, 1)
        self.notes["initial_sync_blocks"] = h
        self.check("a wiped node performs a full initial sync from peers", caught, f"{h}+ blocks in {took:.1f}s")
        quiet = self.quiesce()
        self.check("and ends on exactly the same tip as everyone else", quiet)

    def s_tx_flood(self) -> None:
        self.quiesce()
        a, b = self.accounts["A"], self.accounts["B"]
        self.start_miner(self.nodes[0], a)
        self.start_miner(self.nodes[2], b)
        def available(x: Account) -> int:
            return (self.nodes[0].get(f"/api/v1/account/{x.address}") or {}).get("available_balance", 0)
        rich = wait_until(lambda: available(a) >= 400_000_000, self.scale(240))
        self.check("the funding account has mined and matured enough to fund the flood (>= 400 MAI)", rich,
                   f"{available(a) / 1e6:.1f} MAI available")
        users = [Account() for _ in range(8)]
        expected = {u.address: 0 for u in users}
        fee = 2_000
        nonce = {a.address: (self.nodes[0].get(f"/api/v1/account/{a.address}") or {}).get("next_nonce", 1)}
        accepted = rejected = 0

        def send(tx, via: NetNode) -> bool:
            try:
                r = via.http.post(via.url + "/api/v1/transactions", json=tx)
                return r.status_code == 200
            except Exception:
                return False
        for i, u in enumerate(users):                                  # fund 8 users, 25 MAI each, from the funding account
            tx = a.transfer(u.address, 25_000_000, fee, nonce[a.address])
            nonce[a.address] += 1
            if send(tx, self.nodes[0]):                                 # a wallet sends consecutive transactions via ONE node
                accepted += 1
                expected[u.address] += 25_000_000
            else:
                rejected += 1
        self.event("funding transactions submitted; waiting for them to confirm")
        funded = wait_until(lambda: all((self.nodes[1].get(f"/api/v1/account/{u.address}") or {}).get("balance", 0) >= 25_000_000
                                        for u in users), self.scale(90))
        self.check("8 funding transactions confirm", funded)
        per_user, amount = (10 if self.quick else 20), 100_000
        rng = random.Random(3)
        n_sent = 0
        for u in users:
            via = rng.choice(self.live())                              # each user talks to one node, like a real wallet
            for k in range(per_user):
                dest = rng.choice([x for x in users if x is not u])
                tx = u.transfer(dest.address, amount, fee, k + 1)
                if send(tx, via):
                    accepted += 1
                    n_sent += 1
                    expected[u.address] -= amount + fee
                    expected[dest.address] += amount
                else:
                    rejected += 1
        self.event(f"submitted {n_sent} transfers across 5 nodes ({rejected} rejected at submission)")
        self.check("every submitted transaction was accepted by the node it was sent to", rejected == 0, f"{accepted} ok, {rejected} rejected")
        drained = wait_until(lambda: all((n.status() or {}).get("mempool_size", 1) == 0 for n in self.live()), self.scale(120))
        self.check("all transactions are mined and every mempool drains", drained,
                   str({n.name: (n.status() or {}).get("mempool_size") for n in self.live()}))
        quiet = self.quiesce()
        self.check("nodes agree on the chain after the flood", quiet)
        bad = []
        for u in users:
            for n in self.live():
                bal = (n.get(f"/api/v1/account/{u.address}") or {}).get("balance")
                if bal != expected[u.address]:
                    bad.append((u.address[:10], n.name, bal, expected[u.address]))
        self.check("every account balance is identical on all 5 nodes and equals the expected value", not bad, str(bad[:3]))
        self.notes["tx_flood_transactions"] = accepted

    def s_attack(self) -> None:
        target = self.nodes[1]
        before = (target.metrics() or {}).get("counters", {})
        genesis = PARAMS.genesis_hash

        def hello(**over):
            data = {"min_version": 1, "max_version": 1, "network_id": PARAMS.network_id, "genesis_hash": genesis,
                    "height": 0, "tip_hash": genesis, "total_work": "0", "listen_port": 0,
                    "node_id": os.urandom(16).hex(), "user_agent": "attacker/1"}
            data.update(over)
            return P.encode(1, "hello", data, 100_000)

        def conn():
            s = socket.create_connection(("127.0.0.1", target.p2p_port), timeout=3)
            s.settimeout(2)
            return s

        def closed(s) -> bool:
            try:
                s.settimeout(3)
                while True:
                    if not s.recv(65536):
                        return True
            except socket.timeout:
                return False
            except OSError:
                return True

        def recv_exact(sock, n: int) -> bytes:
            data = b""
            while len(data) < n:
                chunk = sock.recv(n - len(data))
                if not chunk:
                    raise OSError("closed")
                data += chunk
            return data

        def greeted(sock) -> bool:
            """Did the node send its own hello (i.e. did it accept the TCP connection and start the handshake)?"""
            try:
                (length,) = __import__("struct").unpack(">I", recv_exact(sock, 4))
                mtype, _, _ = P.decode(recv_exact(sock, length), None)
                return mtype == "hello"
            except (OSError, P.ProtocolError, ValueError):
                return False

        def send_all(sock, data: bytes) -> bool:
            try:
                sock.sendall(data)
                return True
            except OSError:
                return False                                            # the node already dropped us: acceptable

        def drop_after_closed(sock) -> bool:
            try:
                return closed(sock)
            except OSError:
                return True

        greetings = {}

        def attack(label: str, *payloads: bytes, node_id: str | None = None, gap: float = 0.0):
            sock = conn()
            greetings[label] = greeted(sock)
            for payload in payloads:
                if not send_all(sock, payload):
                    break
                if gap:
                    time.sleep(gap)
            outcomes[label] = drop_after_closed(sock)
            try:
                sock.close()
            except OSError:
                pass

        outcomes = {}
        attack("wrong network", hello(network_id="other-network"))
        attack("wrong genesis", hello(genesis_hash="ab" * 32))
        attack("random bytes", os.urandom(300))
        attack("50 MB length prefix", struct_len(50_000_000))
        attack("HTTP request on the P2P port", frame(b"GET / HTTP/1.1\r\n\r\n"))
        ident = os.urandom(16).hex()
        junk = P.encode(1, "block", {"block": {"height": 1, "junk": True}}, 100_000)
        for rounds in (1, 2):                                          # a peer that keeps sending invalid blocks is banned
            attack(f"invalid-block spam #{rounds}", hello(node_id=ident), junk, junk, junk, gap=0.2)
        attack("banned identity cannot reconnect", hello(node_id=ident))
        ping = P.encode(1, "ping", {"nonce": 1}, 1000)
        attack("ping flood (3000 messages)", hello(node_id=os.urandom(16).hex()), *([ping] * 3000))
        self.check("the node greeted every attacker before rejecting it (each attack really reached the handshake)",
                   all(greetings.values()), str({k: v for k, v in greetings.items() if not v} or "all greeted"))
        for k, v in outcomes.items():
            self.check(f"attack '{k}': connection is dropped", v)
        time.sleep(1)
        after = (target.metrics() or {}).get("counters", {})
        d_pen = sum(after.get("p2p_penalty_points", {"": 0}).values()) - sum(before.get("p2p_penalty_points", {"": 0}).values())
        d_ban = sum(after.get("p2p_peers_banned", {"": 0}).values()) - sum(before.get("p2p_peers_banned", {"": 0}).values())
        self.check("the abuse is visible in the target's metrics (penalty points and bans)", d_pen > 0 and d_ban >= 1,
                   f"+{d_pen} points, +{d_ban} bans")
        self.check("the target node is still healthy and serving the API", (target.get("/api/v1/health") or {}).get("status") == "ok")
        self.start_miner(self.nodes[0], self.accounts["A"])
        h = self.height()
        moved = wait_until(lambda: (target.status() or {}).get("height", 0) > h, self.scale(60))
        self.check("the target keeps following the chain after the attack", moved)
        self.check("all nodes still agree after the attack", self.quiesce())

    def s_corruption(self) -> None:
        self.quiesce()
        victim = self.nodes[2]
        victim.kill()
        db = victim.data_dir / "privnet" / "chain.db"
        # 1. logical tampering: a balance changed behind the node's back
        import sqlite3
        con = sqlite3.connect(db)
        con.execute("UPDATE accounts SET balance = balance + 1 WHERE rowid = (SELECT MIN(rowid) FROM accounts)")
        con.commit(); con.close()
        ok, msg = victim.check_db()
        self.check("tampered balances are detected by `mineai check`", not ok, msg)
        victim.start(self.seeds_for(victim))
        died = wait_until(lambda: not victim.alive, 40, every=0.5)
        self.check("the node REFUSES to start on a tampered database", died, victim.tail_log(2).strip()[-160:])
        # 2. physical damage
        data = bytearray(db.read_bytes())
        for off in range(4096, min(len(data), 4096 * 4), 97):
            data[off] = (data[off] + 77) % 256
        db.write_bytes(bytes(data))
        (victim.data_dir / "privnet" / "chain.db-wal").unlink(missing_ok=True)
        ok, msg = victim.check_db()
        self.check("physical corruption of the database file is detected", not ok, msg)
        victim.start(self.seeds_for(victim))
        died = wait_until(lambda: not victim.alive, 40, every=0.5)
        self.check("the node refuses to start on a corrupted database file", died)
        # 3. recovery: discard the damaged data and resync from peers
        shutil.rmtree(victim.data_dir, ignore_errors=True)
        victim.start(self.seeds_for(victim))
        victim.wait_ready()
        recovered = wait_until(lambda: self.converged(), self.scale(90))
        self.check("after discarding the damaged database the node resyncs and converges", recovered, f"height {self.height()}")

    def s_difficulty(self) -> None:
        """Real hashrate changes on live nodes. The difficulty algorithm has a 30-block window, so each phase must be long
        enough to move it; checks are directional (measured on the chain's own block timestamps and difficulties)."""
        self.quiesce()
        node = self.nodes[0]
        phases = []

        def run_phase(label: str, miners: int, seconds: float):
            h0 = (node.status() or {}).get("height", 0)
            for i in range(miners):
                self.start_miner(self.nodes[i % 5], self.accounts["A"] if i == 0 else Account(), threads=1)
            time.sleep(seconds)
            self.stop_miners()
            wait_until(self.converged, 30)
            h1 = (node.status() or {}).get("height", 0)
            iv = self.block_intervals(node, h0 + 1, h1)
            diffs = self.difficulties(node, h0 + 1, h1)
            third = max(1, len(iv) // 3)
            phases.append({
                "label": label, "miners": miners, "blocks": h1 - h0, "seconds": seconds,
                "interval_mean": round(statistics.mean(iv), 2) if iv else None,
                "interval_first_third": round(statistics.mean(iv[:third]), 2) if iv else None,
                "interval_last_third": round(statistics.mean(iv[-third:]), 2) if iv else None,
                "difficulty_start": diffs[0] if diffs else None, "difficulty_end": diffs[-1] if diffs else None,
                "difficulty_last_third_median": int(statistics.median(diffs[-third:])) if diffs else None})
            self.event(f"phase '{label}': {phases[-1]}")
        run_phase("A: 3 miners", 3, self.scale(120))
        run_phase("B: 10 miners (3.3x hashrate)", 10, self.scale(120))
        run_phase("C: 3 miners again (0.3x hashrate)", 3, self.scale(200))
        self.notes["difficulty_phases"] = phases
        pa, pb, pc = phases
        target = PARAMS.target_spacing
        self.check("phase A: with a matching hashrate the block spacing is near the 5 s target (2.5 s - 12 s)",
                   bool(pa["interval_mean"]) and 2.5 <= pa["interval_mean"] <= 12, f"mean {pa['interval_mean']} s")
        self.check("phase B: blocks speed up right after hashrate rises (first third faster than 70% of target)",
                   bool(pb["interval_first_third"]) and pb["interval_first_third"] <= 0.7 * target, f"{pb['interval_first_third']} s")
        self.check("phase B: difficulty then climbs (last-third median >= 1.3x phase A's)",
                   bool(pb["difficulty_last_third_median"] and pa["difficulty_last_third_median"]) and
                   pb["difficulty_last_third_median"] >= 1.3 * pa["difficulty_last_third_median"],
                   f"{pa['difficulty_last_third_median']:,} -> {pb['difficulty_last_third_median']:,}")
        self.check("phase B: block spacing recovers toward the target by the end (last third within 2 s - 12 s)",
                   bool(pb["interval_last_third"]) and 2 <= pb["interval_last_third"] <= 12, f"{pb['interval_last_third']} s")
        self.check("phase C: blocks slow down right after hashrate falls (first third slower than 1.5x target)",
                   bool(pc["interval_first_third"]) and pc["interval_first_third"] >= 1.5 * target, f"{pc['interval_first_third']} s")
        self.check("phase C: difficulty then falls (last-third median <= 0.9x phase B's)",
                   bool(pc["difficulty_last_third_median"] and pb["difficulty_last_third_median"]) and
                   pc["difficulty_last_third_median"] <= 0.9 * pb["difficulty_last_third_median"],
                   f"{pb['difficulty_last_third_median']:,} -> {pc['difficulty_last_third_median']:,}")

    def s_final(self) -> None:
        self.stop_miners()
        self.check("network converges at the end", wait_until(self.converged, 45))
        self.final_status = {n.name: n.status() for n in self.nodes}
        self.final_metrics = {n.name: n.metrics() for n in self.nodes}
        for n in self.nodes:
            n.kill()
        for n in self.nodes:
            ok, msg = n.check_db()
            self.check(f"offline full verification of {n.name}'s database (`mineai check`)", ok, msg)


def struct_len(n: int) -> bytes:
    import struct
    return struct.pack(">I", n)


def frame(payload: bytes) -> bytes:
    return struct_len(len(payload)) + payload


# ====================================================================== report
def render_report(h: Harness, wall: float) -> str:
    passed = sum(r.passed for r in h.results)
    failed = [r for r in h.results if not r.passed]
    lines = [
        "# Private testnet report (Milestone 7)", "",
        f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')} local time, run time {wall / 60:.1f} minutes"
        f"{' (quick mode)' if h.quick else ''}",
        f"- Network: `privnet` (target block time {PARAMS.target_spacing} s, initial difficulty {PARAMS.difficulty:,}, "
        f"LWMA window {PARAMS.lwma_window}); five separate `mineai node` OS processes on 127.0.0.1",
        f"- Machine: {platform.platform()}, {os.cpu_count()} CPUs, Python {sys.version.split()[0]}",
        f"- **Result: {passed}/{len(h.results)} checks passed" + (f", {len(failed)} FAILED**" if failed else "**"), "",
        "This is a single-machine test: all nodes share one clock and reach each other over loopback with no latency or packet loss. "
        "Partitions are emulated by disabling P2P on a node, not by dropping packets. See Known limitations.", "",
        "## Scenarios", "", "| Scenario | Checks | Time |", "|---|---|---|"]
    for name in h.scenario_times:
        rs = [r for r in h.results if r.scenario == name]
        lines.append(f"| {name} | {sum(r.passed for r in rs)}/{len(rs)} | {h.scenario_times[name]:.0f} s |")
    lines += ["", "## Every check", "", "| Scenario | Check | Result | Detail |", "|---|---|---|---|"]
    for r in h.results:
        lines.append(f"| {r.scenario} | {r.name} | {'PASS' if r.passed else '**FAIL**'} | {r.detail.replace('|', '/')[:150]} |")
    if failed:
        lines += ["", "## Failures", ""] + [f"- **{r.scenario}: {r.name}** — {r.detail}" for r in failed]
    n = h.notes
    lines += ["", "## Measurements", ""]
    if "baseline_interval_mean" in n:
        lines.append(f"- Baseline block interval with 2 miners: mean {n['baseline_interval_mean']} s, median {n['baseline_interval_median']} s "
                     f"(target {PARAMS.target_spacing} s); reorganizations seen by then: {n.get('baseline_reorgs')}")
    if "initial_sync_seconds" in n:
        lines.append(f"- Initial sync of a wiped node: {n['initial_sync_blocks']} blocks in {n['initial_sync_seconds']} s")
    if "tx_flood_transactions" in n:
        lines.append(f"- Transaction flood: {n['tx_flood_transactions']} transactions accepted across 5 nodes, all mined")
    if "difficulty_phases" in n:
        lines += ["", "Difficulty response to real hashrate changes (measured from the chain's block timestamps):", "",
                  "| Phase | Miners | Blocks | Mean interval | First third | Last third | Difficulty start | end | Last-third median |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for p in n["difficulty_phases"]:
            if p["difficulty_start"]:
                lines.append(f"| {p['label']} | {p['miners']} | {p['blocks']} | {p['interval_mean']} s | {p['interval_first_third']} s | "
                             f"{p['interval_last_third']} s | {p['difficulty_start']:,} | {p['difficulty_end']:,} | {p['difficulty_last_third_median']:,} |")
            else:
                lines.append(f"| {p['label']} | {p['miners']} | {p['blocks']} | - | - | - | - | - | - |")
    fm = getattr(h, "final_metrics", {})
    if fm:
        lines += ["", "## Final counters per node", "", "| Node | Height | Blocks accepted | Blocks rejected | Reorgs | Blocks connected in reorgs | Peers banned | Penalty points | Tx accepted | Tx rejected |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for name, m in fm.items():
            if not m:
                lines.append(f"| {name} | (no data) |||||||||")
                continue
            c = m["counters"]
            tot = lambda k: int(sum(c.get(k, {"": 0}).values()))
            lines.append(f"| {name} | {int(m['gauges']['chain_height'])} | {tot('blocks_accepted')} | {tot('blocks_rejected')} | {tot('reorgs')} | "
                         f"{tot('reorg_blocks_connected')} | {tot('p2p_peers_banned')} | {tot('p2p_penalty_points')} | {tot('transactions_accepted')} | {tot('transactions_rejected')} |")
        lines.append("")
        lines.append("Counters restart from zero whenever a node is killed, so they cover only the last run of each process.")
    with h._lock:
        snaps = list(h.snapshots)
    if snaps:
        agree = 0
        worst = cur = 0.0
        last = None
        for s in snaps:
            tips = {(v["height"], v["tip"]) for v in s["nodes"].values() if v}
            heights = {v["height"] for v in s["nodes"].values() if v}
            agreed = len(tips) <= 1
            agree += agreed
            if not agreed:
                cur += 1.0
                worst = max(worst, cur)
            else:
                cur = 0.0
        lines += ["", "## Monitoring summary (1 Hz poll of every node)", "",
                  f"- {len(snaps)} snapshots; all live nodes on the same tip in {100 * agree / len(snaps):.0f}% of them",
                  f"- longest continuous disagreement: about {worst:.0f} s (includes deliberate partitions and restarts)",
                  f"- highest chain height seen: {max((v['height'] for s in snaps for v in s['nodes'].values() if v), default=0)}"]
    lines += ["", "## Timeline", ""] + [f"- `{t:6.0f}s` {text}" for t, text in h.timeline]
    lines += ["", "## Known limitations of this test", "",
              "- One machine, one clock, loopback networking: no latency, jitter, packet loss, or clock skew was exercised.",
              "- Partitions are emulated by disabling a node's P2P layer, not by blocking traffic between live peers.",
              "- Hashrate comes from single-threaded Python CPU miners; the absolute numbers say nothing about a real network.",
              "- Five nodes, one operator. Nothing here tests independently operated nodes or hostile majority behaviour.",
              "- Disk-full, out-of-memory, and power-loss (as opposed to process-kill) failures were not tested."]
    return "\n".join(lines) + "\n"


# ====================================================================== commands
def cmd_run(args) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="mineai-privnet-"))
    h = Harness(workdir, args.quick)
    only = set(args.only.split(",")) if args.only else None
    steps = [("setup", h.s_setup), ("baseline", h.s_baseline), ("kill_recovery", h.s_kill_recovery),
             ("rolling_restart", h.s_rolling_restart), ("partition", h.s_partition), ("late_joiner", h.s_late_joiner),
             ("tx_flood", h.s_tx_flood), ("attack", h.s_attack), ("corruption", h.s_corruption),
             ("difficulty", h.s_difficulty), ("final", h.s_final)]
    log(f"work directory {workdir}")
    poller = threading.Thread(target=h.poller, daemon=True)
    started = time.time()
    try:
        for name, fn in steps:
            if only and name not in only and name not in ("setup", "final"):
                continue
            if name == "setup" or h.nodes:
                h.run_scenario(name, fn)
            if name == "setup":
                poller.start()
    finally:
        h._stop_poll.set()
        h.stop_miners()
        for n in h.nodes:
            n.kill()
    wall = time.time() - started
    report = render_report(h, wall)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    md, js = out / f"private-testnet-{stamp}.md", out / f"private-testnet-{stamp}.json"
    md.write_text(report, encoding="utf-8")
    js.write_text(json.dumps({"results": [r.__dict__ for r in h.results], "notes": h.notes, "timeline": h.timeline,
                              "snapshots": h.snapshots[::5]}, indent=1, default=str), encoding="utf-8")
    failed = [r for r in h.results if not r.passed]
    log(f"report: {md}")
    log(f"{len(h.results) - len(failed)}/{len(h.results)} checks passed")
    for r in failed:
        log(f"  FAILED [{r.scenario}] {r.name}: {r.detail}")
        for n in h.nodes:
            pass
    if failed and args.keep_logs:
        log(f"node logs kept in {workdir}")
    elif not failed:
        shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failed else 0


def cmd_monitor(args) -> None:
    urls = [f"http://127.0.0.1:{args.base_port + i}" for i in range(args.nodes)]
    with httpx.Client(timeout=1.5) as c:
        while True:
            print(f"\n{time.strftime('%H:%M:%S')}  {'node':<6}{'height':>7} {'tip':<13}{'peers':>5}{'mempool':>8}{'difficulty':>12}{'reorgs':>7}  sync")
            for i, u in enumerate(urls):
                try:
                    s = c.get(u + "/api/v1/status").json()
                    print(f"          n{i + 1:<5}{s['height']:>7} {s['latest_hash'][:12]:<13}{s['peer_count']:>5}{s['mempool_size']:>8}"
                          f"{s['difficulty']:>12,}{s['reorgs_since_start']:>7}  {s['sync_status']}")
                except Exception:
                    print(f"          n{i + 1:<5}  (down)")
            time.sleep(args.interval)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the full private-testnet test program and write a report")
    r.add_argument("--quick", action="store_true", help="shorter timings (smoke run)")
    r.add_argument("--only", default=None, help="comma-separated scenarios (setup and final always run)")
    r.add_argument("--out", default=str(ROOT / "docs" / "reports"))
    r.add_argument("--keep-logs", action="store_true")
    m = sub.add_parser("monitor", help="live table of a running private testnet")
    m.add_argument("--base-port", type=int, default=API_BASE)
    m.add_argument("--nodes", type=int, default=5)
    m.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args()
    try:
        if args.cmd == "run":
            sys.exit(cmd_run(args))
        cmd_monitor(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
