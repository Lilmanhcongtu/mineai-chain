# MineAI 0.2.0rc1 — public testnet release candidate

**This is a release candidate for a test network. It is not production software, has not been independently audited, and
its coins (MAI on `testnet`) have no monetary value.** Nothing has been launched: there are no seed nodes, no hosted
infrastructure and no public network yet. See `LAUNCH_CRITERIA.md` for what is still required.

## What this release contains

| Area | State |
|---|---|
| Consensus | Account model, Ed25519 signatures bound to the network id, integer money (6 decimals), 25 MAI subsidy, 100,000,000 MAI cap, no premine, coinbase maturity 10 (testnet), median-time-past and +5 min timestamp rules |
| Proof of work | SHA-256 with a numeric difficulty target; per-block retargeting to ~60 s (LWMA over median-filtered timestamps). RandomX is evaluated, **not** implemented |
| Fork handling | Cumulative-work chain selection, side chains, orphans, atomic reorganizations (depth limit 100), mempool restoration |
| Networking | TCP P2P with handshake, discovery, persisted peers, relay, locator sync, rate limits, decaying misbehavior scores and temporary bans. **Unencrypted and unauthenticated** |
| Storage | SQLite, forward-only migrations (schema v4), integrity checks on open, `mineai check` offline verifier |
| Wallet | Encrypted keys (scrypt + AES-GCM), locking with auto-lock, verified backup/restore, atomic password change, transfer preview, history, interactive shell. No recovery phrase |
| Miner | User-started CPU miner, pluggable backend, stale-work detection, clear rejection reasons |
| Explorer / API | Read-only explorer (blocks, transactions, addresses, search), versioned `/api/v1`, Prometheus `/metrics` |
| Packaging | Windows package (unsigned) with installer and checksums; Linux source install scripts (**not verified on Linux**); source archive |

## Testnet identity (frozen for this release)

* Network id `mineai-testnet-v1`, address prefix `TMAI`, API port 18080, P2P port 18081, data in `<data dir>/testnet/`.
* Genesis hash `2ddd2b532e7d313ab4fed3c7ddbba84297d9f35fd040292e2046da471d29d335` (see `TESTNET_GENESIS.md`).
* The consensus rules are pinned by a fingerprint test. **Any rule change requires a new network id and a testnet reset.**
* V0.1 / devnet / privnet balances and wallets do not carry over (different network ids, prefixes and genesis blocks).

## Verification performed

* 600+ automated tests (unit, consensus, integration, network with real sockets, adversarial), ~93% line coverage.
* A private five-process network was run through hard kills, rolling restarts, partitions, a wiped-node resync, a transaction flood,
  P2P attacks, database corruption and hashrate changes; the final run passed 60/60 checks (`reports/`). Earlier runs of that
  program found four real bugs, all fixed with regression tests.
* The Windows package was built, checksum-verified, installed from the zip and exercised end to end with only the packaged executables.

## What is NOT verified

Linux (no Linux environment was available), any real multi-host network, latency/packet-loss/clock-skew, disk-full and
power-loss failures, hostile-majority behaviour, long-duration operation (the 60-90 day soak), and external security review.
See `KNOWN_LIMITATIONS.md` and `SECURITY_CHECKLIST.md`.

## Upgrade notes

There is nothing to upgrade from: earlier networks (V0.1 local devnet, devnet v2/v3) are intentionally incompatible.
Databases from those versions are refused, never modified.

## Integrity

`SHA256SUMS.txt` lists every artifact. Artifacts are **not code-signed** and the build is not reproducible. Compare hashes
against a copy obtained from a channel you trust before running anything.

## Late fix before tagging

A stress run of the extracted source archive exposed a flaky test (about 3 failures in 16 runs under CPU load). Cause: a real
P2P bug. When two nodes dialed each other at the same instant, each rejected the other's connection as a duplicate and the
link was lost. Both sides now keep the connection dialed by the lower node id. Regression test:
`test_simultaneous_dials_leave_exactly_one_connection_not_zero` (fails on the old code). After the fix: 0 failures in 20
loaded runs, 614 tests passing, and the private testnet again 60/60 (`docs/reports/private-testnet-20260919-1927.md`).
