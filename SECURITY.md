# Security

**MineAI is prototype software for development networks. It is not production-ready, has not been
independently audited, and must never hold real monetary value.**

## Reporting a vulnerability

Do not open a public issue for security problems. Contact the project owner privately with a description,
affected version/commit, and reproduction steps. Never include real private keys, passwords or wallet
files in a report. (A dedicated contact address and response process will be published before any public testnet.)

## Never share

Wallet files (`*.wallet.json`), passwords, private keys, recovery information, `.env`, database files.
They are excluded by `.gitignore`; do not force-add them. Logs are structured and the logger refuses fields named
like `password`, `private_key`, `secret`, `mnemonic` or `seed`.

## What is defended today (all covered by automated tests)

* Integer-only money (atomic units), strict integer type/range checks on every consensus field, no floats.
* Deterministic, domain-separated binary encodings for signatures, txids and block hashes (`PROTOCOL.md`).
* Network-bound signatures: no cross-network replay. Network-specific address prefixes.
* Canonical-only addresses, base64 and hex: no aliases, no malleable encodings.
* Nonce-based double-spend and replay protection; duplicate/conflicting transaction rejection.
* Block validation of PoW, difficulty, merkle root, coinbase, subsidy, fees, supply cap, timestamps, sizes.
* Coinbase maturity.
* Atomic persistence (block + state + mempool in one SQLite transaction), forward-only migrations, integrity checks on open, refusal of foreign/legacy/newer databases.
* Chain: cumulative-work fork choice, atomic reorganizations that roll back completely on any invalid block, permanent rejection of invalid branches (time-dependent failures excepted), reorg-depth and side-block/orphan bounds, no switching on equal work.
* P2P: length checked before reading any body, strict allow-list message schemas, size/time/rate limits, connection limits, misbehavior scores that survive reconnects, temporary bans, unsolicited-response rejection, duplicate-request suppression, mandatory handshake with network/genesis/version checks, no administrative messages, loopback binding by default.
* API: strict parsing, request-size and rate limits, uniform error responses without stack traces, loopback-only mining endpoints, no admin or wallet endpoints.
* Wallet: scrypt (N=2^17) + AES-256-GCM, ciphertext bound to address and network, no overwrite of existing files, password never on the command line.

## Known limitations (be honest about these)

1. **Fork handling is new and only tested on localhost.** Cumulative-work selection, reorganizations (atomic, depth-limited to 100 blocks) and side-block storage have had no external review. The depth limit protects against deep-fork attacks but can split the network permanently if a partition outlasts it. Orphan blocks are held in memory (up to 100 blocks, so up to ~50 MB in the worst case) and side blocks on disk (up to 2000).
2. **Difficulty adjustment is simulated, not proven.** The 60 s target, hashrate steps, pauses and timestamp attacks were tested with seeded simulations and a real 3-process run, never on a real network with real, adversarial hashrate. A near-majority attacker can slow blocks by ~1.5x by backdating timestamps; a >5-minute fast clock gets a node's blocks rejected; a 90% hashrate collapse takes ~30 slow blocks to absorb (no emergency adjustment). The algorithm has had no external review.
3. **Wallet:** no lock/timeout, no backup/restore tooling beyond copying the file, no recovery phrase, no hardware-wallet support. Password strength is only length-checked (≥ 10 chars). Unlocked keys live in process memory while signing.
4. **Address format and encodings are project-specific and unreviewed** by cryptographers.
5. **Mempool:** no replace-by-fee, no eviction by fee rate (new transactions are refused when full).
6. **Rate limiting is in-process and per-IP** and is not a substitute for network-level protection. **P2P has no encryption or peer authentication:** traffic is plaintext and node ids are self-declared, so a peer can lie about its height and an on-path attacker can read or drop messages. There is no eclipse-attack protection (no address diversity rules, no anchor connections) and bans by node id can be evaded by generating new ids (non-loopback IPs are also banned). Use only on trusted/local networks.
7. **Loopback check** for mining endpoints trusts the socket peer address; a reverse proxy defeats it.
8. **Windows script and CPU-miner paths** are exercised by a manual end-to-end demo, not by unit tests.
9. **Timestamp validation against wall-clock time** makes validity slightly time-dependent.
10. No independent security audit. Do not use for value.

## Supported versions

Only the current `main` development line.
