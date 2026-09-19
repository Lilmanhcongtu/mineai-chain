"""CPU miner. It mines ONLY when you run it, only for the address you give it, with the thread count you choose,
and stops on Ctrl+C. There is no background mode, no auto-start and no hidden mining anywhere in MineAI."""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import queue
import time

import httpx

from . import config
from .consensus import header_prefix
from .crypto import validate_address
from .pow import get_backend

COUNT_CHUNK = 512


def mine_worker(backend_name: str, prefix: bytes, difficulty: int, worker_id: int, workers: int, stop, result, counter):
    backend = get_backend(backend_name)

    def progress(n: int) -> None:
        counter.value += n

    found = backend.search(prefix, difficulty, worker_id, workers, stop.is_set, progress)
    if found is not None:
        result.put(found)
        stop.set()


def mine_template(params, template: dict, threads: int, should_abort=lambda: False, backend: str = "sha256"):
    """Search for a valid nonce. Returns (block, seconds, hashrate) or None if aborted (stale work)."""
    prefix = header_prefix(params, template["height"], template["previous_hash"],
                           template["merkle_root"], template["timestamp"], template["difficulty"])
    stop, result = mp.Event(), mp.Queue()
    counters = [mp.Value("Q", 0, lock=False) for _ in range(threads)]
    procs = [mp.Process(target=mine_worker, daemon=True,
                        args=(backend, prefix, template["difficulty"], i, threads, stop, result, counters[i]))
             for i in range(threads)]
    started = time.perf_counter()
    for p in procs:
        p.start()
    found = None
    try:
        while found is None:
            try:
                found = result.get(timeout=1.0)
            except queue.Empty:
                if should_abort():
                    return None
    finally:
        stop.set()
        for p in procs:
            p.join(timeout=2)
            if p.is_alive():
                p.terminate()
    elapsed = max(time.perf_counter() - started, 1e-9)
    total = sum(c.value for c in counters)
    nonce, digest = found
    block = {k: template[k] for k in ("height", "previous_hash", "merkle_root", "timestamp",
                                      "difficulty", "transactions")}
    block.update(nonce=nonce, hash=digest)
    return block, elapsed, total / elapsed


def _reason(response) -> tuple[str, str]:
    try:
        err = response.json()["error"]
        return err.get("code", str(response.status_code)), err.get("message", "")
    except (ValueError, KeyError, TypeError):
        return str(response.status_code), response.text[:200]


def run_miner(params, node: str, address: str, blocks: int, threads: int, backend: str = "sha256",
              out=print, http=httpx, miner=mine_template) -> int:
    """Mine until `blocks` blocks were accepted. Returns the number accepted. Raises SystemExit on hard errors."""
    accepted = 0
    while accepted < blocks:
        r = http.get(node + "/api/v1/mining/template", params={"address": address}, timeout=10)
        if r.status_code >= 400:
            code, message = _reason(r)
            raise SystemExit(f"node refused to give a mining template [{code}]: {message}")
        template = r.json()
        if template.get("network_id") != params.network_id:
            raise SystemExit("node is on a different network than the miner expects; refusing to mine")
        out(f"Mining block {template['height']} | difficulty {template['difficulty']:,} | "
            f"txs {len(template['transactions']) - 1}")

        def stale(prev=template["previous_hash"], last=[time.monotonic()]):
            if time.monotonic() - last[0] < 3:
                return False
            last[0] = time.monotonic()
            try:
                return http.get(node + "/api/v1/status", timeout=5).json()["latest_hash"] != prev
            except Exception:
                return False

        outcome = miner(params, template, threads, stale, backend)
        if outcome is None:
            out("Stale work (a new block arrived): dropping it and fetching a fresh template.")
            continue
        block, elapsed, rate = outcome
        r = http.post(node + "/api/v1/mining/submit", json=block, timeout=20)
        if r.status_code >= 400:
            code, message = _reason(r)
            if code == "stale":
                out(f"Rejected [stale]: another block was accepted first ({message}). Retrying with fresh work.")
                continue
            raise SystemExit(f"block REJECTED [{code}]: {message}")
        accepted += 1
        body = r.json()
        out(f"ACCEPTED block {body['height']} {body['hash'][:18]}… | {elapsed:.2f}s | {rate:,.0f} H/s")
    return accepted


def main() -> None:
    mp.freeze_support()
    ap = argparse.ArgumentParser(description="MineAI CPU miner. Runs only while this command runs.")
    ap.add_argument("--address", required=True, help="MAI address that receives the rewards")
    ap.add_argument("--network", default=None, help="devnet | testnet (default: MINEAI_NETWORK or devnet)")
    ap.add_argument("--node", default=None)
    ap.add_argument("--blocks", type=int, default=1, help="stop after this many accepted blocks")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2),
                    help="worker processes (default: half the CPUs)")
    ap.add_argument("--backend", default="sha256", help="proof-of-work search backend (only 'sha256' exists)")
    args = ap.parse_args()

    params = config.get_params(args.network)
    node = (args.node or f"http://127.0.0.1:{params.default_port}").rstrip("/")
    if not validate_address(args.address, params.address_prefix):
        raise SystemExit(f"invalid {params.name} address (expected prefix {params.address_prefix})")
    cpus = os.cpu_count() or 1
    if args.blocks < 1 or not 1 <= args.threads <= 256:
        raise SystemExit("--blocks must be >= 1 and --threads between 1 and 256")
    try:
        get_backend(args.backend)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if args.threads > cpus:
        print(f"Note: {args.threads} threads on {cpus} CPUs will not go faster.")

    print(f"[{params.label}] mining {args.blocks} block(s) to {args.address} with {args.threads} worker(s). "
          "Press Ctrl+C to stop; nothing keeps mining after this command exits.")
    try:
        run_miner(params, node, args.address, args.blocks, args.threads, args.backend)
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
