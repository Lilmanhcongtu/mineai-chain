# MineAI (V0.2 development line)

Prototype blockchain software for **development networks only** — MineAI (`MAI`), account-based ledger,
CPU SHA-256 proof of work, Ed25519 wallets, REST API and a local explorer.

> **Not production software.** No independent audit, no dynamic difficulty yet, only tested on localhost.
> Coins on devnet/testnet have **no monetary value**. There is no mainnet, no presale and nothing to buy.

Status: Milestone 4 (fork choice and reorganizations) — see `PROTOCOL.md` for the exact rules, `TOKENOMICS.md`,
`NETWORK.md`, `SECURITY.md` (including known limitations) and `CONTRIBUTING.md`.

## What changed from V0.1

* Integer-only money; deterministic binary encodings for signatures, txids, merkle root and block hash.
* Network ID in every signature/hash and network-specific address prefixes (no cross-network replay).
* Strict validation of every consensus field, timestamp rules, coinbase maturity, size limits.
* Incremental account state (no chain replay per request), atomic block commits, versioned migrations, integrity checks.
* Hardened API (size/rate limits, uniform errors, loopback-only mining), hardened wallet (scrypt 2^17, AES-GCM, no overwrite, no `--password` flag), structured logging.
* **V0.1 databases and wallets are intentionally incompatible** (different network, addresses and formats). Nothing carries over; V0.1 files are never modified.

* **Milestone 3:** multi-node P2P (handshake, discovery, tx/block relay, sync, bans) - see `PROTOCOL.md` section 10.
* **Milestone 4:** cumulative-work chain selection, side chains, orphans, atomic reorganizations, mempool restoration - see `PROTOCOL.md` section 8.

## Quick start (Windows PowerShell, Python 3.10+)

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1        # venv + install; stops on any failure
.\scripts\demo_windows.ps1         # isolated end-to-end demo (node, 2 wallets, mining, transfer)
.\scripts\demo_three_nodes.ps1     # three node processes: relay, sync, a partition and a real reorganization
```

Run your own devnet node:

```powershell
.\scripts\start_node.ps1           # http://127.0.0.1:8080  (explorer)  /docs (API docs)
```

In a second window (you are prompted for wallet passwords; use a throwaway one):

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m mineai.wallet create --wallet alice.wallet.json
& $py -m mineai.wallet create --wallet bob.wallet.json
& $py -m mineai.wallet address --wallet alice.wallet.json
& $py -m mineai.miner --address <ALICE_ADDRESS> --blocks 4          # explicit, stops after 4 blocks; Ctrl+C to stop
& $py -m mineai.wallet balance --wallet alice.wallet.json
& $py -m mineai.wallet send --wallet alice.wallet.json --to <BOB_ADDRESS> --amount 5   # shows a preview, asks to confirm
& $py -m mineai.miner --address <ALICE_ADDRESS> --blocks 1
```

Newly mined rewards are *immature* for a few blocks (devnet: 3) and cannot be spent yet; the wallet shows this.
Wallet files (`*.wallet.json`) are git-ignored: back them up yourself and never share them.

## Configuration

Environment variables (see `.env.example`): `MINEAI_NETWORK` (`devnet`/`testnet`), `MINEAI_HOST`, `MINEAI_PORT`,
`MINEAI_DATA_DIR`, `MINEAI_P2P`, `MINEAI_P2P_HOST`, `MINEAI_P2P_PORT`, `MINEAI_SEEDS`, `MINEAI_MAX_PEERS`, `MINEAI_MAX_BODY_BYTES`, `MINEAI_RATE_LIMIT_PER_MINUTE`, `MINEAI_LOG_LEVEL`.
Consensus parameters are **not** configurable: they are fixed per network profile.
Data lives in `<data dir>/<network>/chain.db` (default `~/.mineai`).

## API (devnet)

```text
GET  /api/health  /api/ready  /api/status
GET  /api/blocks?limit&offset        GET /api/block/{height|hash}
GET  /api/tx/{txid}                  GET /api/mempool?limit&offset
GET  /api/account/{address}          POST /api/transactions
GET  /api/peers                      GET /api/mining/template?address=   POST /api/mining/submit      (loopback clients only)
```

Errors are uniform: `{"error": {"code": "...", "message": "..."}}`. API versioning and OpenAPI polish are planned for Milestone 9.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest --cov=mineai --cov-report=term-missing
```

Suites: `tests/unit` (encodings, golden vectors, addresses, amounts), `tests/consensus` (every rule with a violating input),
`tests/integration` (persistence, migrations, corruption, atomicity, API, wallet), `tests/network` (real-socket multi-node tests: handshake, relay, sync, recovery, malformed input, flooding, bans), `tests/adversarial` (resource exhaustion, malformed input, abuse).

## Roadmap

3 (done): three-node P2P devnet → 4 (done): fork choice and reorgs → 5: dynamic difficulty (~60 s) → 6: wallet, miner, explorer → 7: private testnet →
8: public testnet candidate. Mainnet is **not** planned for launch without explicit authorization and an independent audit.
