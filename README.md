# MineAI (V0.2 development line)

Prototype blockchain software for **development networks only** — MineAI (`MAI`), account-based ledger,
CPU SHA-256 proof of work, Ed25519 wallets, REST API and a local explorer.

> **Not production software.** No independent audit, only tested on localhost and in simulation.
> Coins on devnet/testnet have **no monetary value**. There is no mainnet, no presale and nothing to buy.

Status: **0.2.0rc1, public testnet release candidate (not launched)** — see `docs/RELEASE_NOTES.md`, `docs/KNOWN_LIMITATIONS.md`,
`docs/SECURITY_CHECKLIST.md` and `docs/LAUNCH_CRITERIA.md` first. See `PROTOCOL.md` for the exact rules, `TOKENOMICS.md`,
`NETWORK.md`, `SECURITY.md` (including known limitations) and `CONTRIBUTING.md`.

## License

MIT, see `LICENSE`. The software comes with no warranty; the licence does not change the fact that this is test software for networks with no monetary value.

## What changed from V0.1

* Integer-only money; deterministic binary encodings for signatures, txids, merkle root and block hash.
* Network ID in every signature/hash and network-specific address prefixes (no cross-network replay).
* Strict validation of every consensus field, timestamp rules, coinbase maturity, size limits.
* Incremental account state (no chain replay per request), atomic block commits, versioned migrations, integrity checks.
* Hardened API (size/rate limits, uniform errors, loopback-only mining), hardened wallet (scrypt 2^17, AES-GCM, no overwrite, no `--password` flag), structured logging.
* **V0.1 databases and wallets are intentionally incompatible** (different network, addresses and formats). Nothing carries over; V0.1 files are never modified.

* **Milestone 3:** multi-node P2P (handshake, discovery, tx/block relay, sync, bans) - see `PROTOCOL.md` section 10.
* **Milestone 4:** cumulative-work chain selection, side chains, orphans, atomic reorganizations, mempool restoration - see `PROTOCOL.md` section 8.
* **Milestone 8:** release candidate 0.2.0rc1: testnet genesis pinned and its consensus parameters frozen by a fingerprint test, TESTNET labelling on every tool, release/install/troubleshooting documents, security checklist, launch criteria, a release script that produces checksummed artifacts. Linux scripts exist but are **unverified**; artifacts are **unsigned**.
* **Milestone 7:** a private five-node test network (`privnet` profile, 5 s blocks) driven by `scripts\private_testnet.py`: hard crashes, rolling restarts, partitions, a wiped-node resync, a transaction flood, P2P attacks, database corruption and hashrate changes, with a written report in `docs/reports/`. Adds `/metrics` (Prometheus) and `/api/v1/metrics`, and `mineai check` to verify a database offline. See `docs/PRIVATE_TESTNET.md`.
* **Milestone 6:** hardened wallet (locking, auto-lock, verified backup/restore, password change, interactive shell), user-controlled miner behind a pluggable proof-of-work backend, a full read-only explorer, a versioned `/api/v1`, and a Windows package with installer and checksums. RandomX is evaluated in `docs/RANDOMX_EVALUATION.md` and is **not** implemented.
* **Milestone 5:** numeric difficulty target and per-block retargeting to ~60 s (LWMA over median-filtered timestamps) - see `PROTOCOL.md` section 5.10. Devnet was reset for this change (network id `mineai-devnet-v3`); older devnet databases are refused.

## Quick start (Windows PowerShell, Python 3.10+)

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1        # venv + install; stops on any failure
.\scripts\demo_windows.ps1         # isolated end-to-end demo (node, 2 wallets, mining, transfer)
.\scripts\demo_three_nodes.ps1     # three node processes: relay, sync, a partition and a real reorganization
```

Run your own devnet node (explorer at `http://127.0.0.1:8080`, API docs at `/docs`):

```powershell
.\scripts\start_node.ps1
```

### Wallet

```powershell
& python -m mineai wallet create   --wallet me.wallet.json          # hidden password prompt, 10+ chars, weak ones refused
& python -m mineai wallet address  --wallet me.wallet.json
& python -m mineai wallet balance  --wallet me.wallet.json          # confirmed / immature / pending / available
& python -m mineai wallet history  --wallet me.wallet.json          # pending first, then confirmed, with confirmations
& python -m mineai wallet send     --wallet me.wallet.json --to <ADDRESS> --amount 5   # preview + confirmation
& python -m mineai wallet shell    --wallet me.wallet.json --lock-timeout 120           # interactive; auto-locks when idle
& python -m mineai wallet backup   --wallet me.wallet.json --to D:\safe\me.backup.json --verify-password
& python -m mineai wallet restore  --from D:\safe\me.backup.json --wallet restored.wallet.json
& python -m mineai wallet verify   --wallet me.wallet.json
& python -m mineai wallet change-password --wallet me.wallet.json
```

* Keys are generated from the OS CSPRNG, stored encrypted (scrypt N=2^17 + AES-256-GCM, bound to the address and network) and signed locally. Passwords are never accepted on the command line and never printed.
* Wallet files are created owner-only, never overwritten, and backups/restores are verified copies that also refuse to overwrite.
* `send` always shows a preview (network, from, to, amount, fee, total) and asks you to type `yes`. Amounts must be plain decimals (`5`, `0.25`; no `1e3`).
* There is **no recovery phrase**: the encrypted file plus its password is the only backup. An established standard (BIP-39) may be added later; a custom format never will.
* Newly mined rewards are *immature* for a few blocks (devnet: 3) and cannot be spent yet; the wallet shows this.

### Miner

```powershell
& python -m mineai miner --address <ADDRESS> --blocks 4 --threads 2      # stops after 4 accepted blocks; Ctrl+C stops earlier
```

It mines only while the command runs, only to the address you give, with the thread count you choose (default: half the CPUs). It reports the aggregate hashrate, drops stale work when another block arrives, and prints why a block was rejected. There is no background or automatic mining anywhere in MineAI.

### Explorer

Open `http://127.0.0.1:8080`: network, node version, height, difficulty, estimated hashrate, circulating and maximum supply, mempool size, peer count and sync status; paginated latest blocks; block, transaction and address pages with confirmations and history; a search box for block heights/hashes, transaction ids and addresses. It is read-only: no keys, no forms other than search, no scripts.

### Windows package

```powershell
.\scripts\build_windows.ps1                       # tests, then dist\MineAI-<version>-windows\ and .zip (+ SHA-256 checksums)
```

The package contains `mineai.exe` (node, wallet and miner in one), per-tool `.cmd` wrappers, `install.ps1`/`uninstall.ps1` (per-user, no administrator rights, no services, nothing auto-starts, your wallets and chain data are never deleted), documentation and `SHA256SUMS.txt`. It is **not code-signed**; see `README-WINDOWS.txt` inside the package.

## Configuration

Environment variables (see `.env.example`): `MINEAI_NETWORK` (`devnet`/`testnet`), `MINEAI_HOST`, `MINEAI_PORT`,
`MINEAI_DATA_DIR`, `MINEAI_P2P`, `MINEAI_P2P_HOST`, `MINEAI_P2P_PORT`, `MINEAI_SEEDS`, `MINEAI_MAX_PEERS`, `MINEAI_MAX_BODY_BYTES`, `MINEAI_RATE_LIMIT_PER_MINUTE`, `MINEAI_LOG_LEVEL`.
Consensus parameters are **not** configurable: they are fixed per network profile.
Data lives in `<data dir>/<network>/chain.db` (default `~/.mineai`).

## API (devnet)

```text
GET  /api/v1/health  /ready  /status  /fee  /peers        GET /api/v1/search?q=
GET  /api/v1/blocks?limit&offset   /block/{height|hash}   /tx/{txid}   /mempool?limit&offset
GET  /api/v1/account/{address}     /address/{address}/transactions?limit&offset
POST /api/v1/transactions
GET  /api/v1/mining/template?address=     POST /api/v1/mining/submit      (loopback clients only)
```

Errors are uniform: `{"error": {"code": "...", "message": "..."}}`. The API is versioned under `/api/v1` (the unversioned `/api/...` paths are kept as hidden aliases); OpenAPI docs are at `/docs`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest --cov=mineai --cov-report=term-missing
```

Suites: `tests/unit` (encodings, golden vectors, addresses, amounts), `tests/consensus` (every rule with a violating input),
`tests/integration` (persistence, migrations, corruption, atomicity, API, wallet), `tests/network` (real-socket multi-node tests: handshake, relay, sync, recovery, malformed input, flooding, bans), `tests/adversarial` (resource exhaustion, malformed input, abuse).

## Roadmap

3 (done): three-node P2P devnet → 4 (done): fork choice and reorgs → 5 (done): dynamic difficulty → 6 (done): wallet, miner, explorer → 7 (done): private testnet →
8: public testnet candidate. Mainnet is **not** planned for launch without explicit authorization and an independent audit.
