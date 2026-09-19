"""CPU SHA-256 miner (development PoW). Runs only when you start it; stops on Ctrl+C."""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import queue
import time

import httpx

from . import config
from .consensus import hash_header, header_prefix, meets_target
from .crypto import validate_address
from .util import atomic_to_mai

DEFAULT_NODE = "http://127.0.0.1:8080"
COUNT_EVERY = 2048


def mine_worker(prefix: bytes, difficulty: int, worker_id: int, workers: int, stop, result, counter):
    nonce, hashes = worker_id, 0
    while not stop.is_set():
        digest = hash_header(prefix, nonce)
        hashes += 1
        if meets_target(digest, difficulty):
            counter.value += hashes
            result.put((nonce, digest))
            stop.set()
            return
        if hashes % COUNT_EVERY == 0:
            counter.value += COUNT_EVERY
            hashes -= COUNT_EVERY
        nonce += workers


def mine_template(params, template: dict, threads: int, should_abort=lambda: False):
    """Returns (block, seconds, hashrate) or None if aborted (stale work)."""
    prefix = header_prefix(params, template["height"], template["previous_hash"],
                           template["merkle_root"], template["timestamp"], template["difficulty"])
    stop, result = mp.Event(), mp.Queue()
    counters = [mp.Value("Q", 0, lock=False) for _ in range(threads)]
    procs = [mp.Process(target=mine_worker, daemon=True,
                        args=(prefix, template["difficulty"], i, threads, stop, result, counters[i]))
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


def main() -> None:
    mp.freeze_support()
    ap = argparse.ArgumentParser(description="MineAI CPU miner (development proof of work)")
    ap.add_argument("--address", required=True, help="MAI reward address")
    ap.add_argument("--network", default=None, help="devnet | testnet (default: MINEAI_NETWORK or devnet)")
    ap.add_argument("--node", default=None)
    ap.add_argument("--blocks", type=int, default=1, help="stop after this many accepted blocks")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = ap.parse_args()

    params = config.get_params(args.network)
    node = (args.node or f"http://127.0.0.1:{params.default_port}").rstrip("/")
    if not validate_address(args.address, params.address_prefix):
        raise SystemExit(f"invalid {params.name} address (expected prefix {params.address_prefix})")
    if args.blocks < 1 or args.threads < 1 or args.threads > 256:
        raise SystemExit("--blocks must be >= 1 and --threads between 1 and 256")

    print(f"[{params.label}] mining {args.blocks} block(s) with {args.threads} worker(s). Press Ctrl+C to stop.")
    accepted = 0
    try:
        while accepted < args.blocks:
            r = httpx.get(node + "/api/mining/template", params={"address": args.address}, timeout=10)
            if r.status_code >= 400:
                raise SystemExit(f"node refused template: {r.text}")
            template = r.json()
            if template.get("network_id") != params.network_id:
                raise SystemExit("node is on a different network than the miner expects; refusing to mine")
            print(f"Mining block {template['height']} | difficulty {template['difficulty']} | "
                  f"txs {len(template['transactions']) - 1}")

            def stale(prev=template["previous_hash"], last=[time.monotonic()]):
                if time.monotonic() - last[0] < 3:
                    return False
                last[0] = time.monotonic()
                try:
                    return httpx.get(node + "/api/status", timeout=5).json()["latest_hash"] != prev
                except Exception:
                    return False

            outcome = mine_template(params, template, args.threads, stale)
            if outcome is None:
                print("Stale work (a new block arrived); fetching a fresh template.")
                continue
            block, elapsed, rate = outcome
            r = httpx.post(node + "/api/mining/submit", json=block, timeout=20)
            if r.status_code >= 400:
                err = r.json().get("error", {}) if r.headers.get("content-type", "").startswith("application/json") else {}
                if err.get("code") == "stale":
                    print("Rejected: stale block; retrying with a fresh template.")
                    continue
                raise SystemExit(f"block rejected [{err.get('code', r.status_code)}]: {err.get('message', r.text)}")
            accepted += 1
            print(f"Accepted block {r.json()['height']} {r.json()['hash'][:18]}… | {elapsed:.2f}s | {rate:,.0f} H/s")
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
