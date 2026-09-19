# Troubleshooting

Commands use the Windows package names (`mineai-node.cmd`, ...). From source use `python -m mineai node|wallet|miner|check`.

| Symptom | Likely cause | What to do |
|---|---|---|
| Windows warns "unknown publisher" / SmartScreen blocks it | The package is unsigned | Verify the SHA-256 against `SHA256SUMS.txt`; run only if it matches |
| `unknown network` / `the mainnet profile is disabled` | Wrong `MINEAI_NETWORK` / `--network` | Use `devnet`, `testnet` or `privnet`. Mainnet does not exist |
| Node exits at start: `database belongs to network 'X'` | The data directory belongs to another network | Each network has its own folder `<data dir>/<network>/`; do not point networks at the same file |
| Node exits: `legacy V0.1 database` / `newer than this software` | An old or foreign database | Use a fresh data directory. Old databases are never modified |
| Node exits: `database is corrupted` / `sum of balances differs from minted supply` / `tip block hash does not verify` | Damaged or modified database | Do not edit it. Move `chain.db*` aside and restart; the node resyncs from peers. `mineai check` explains the exact problem |
| Height not increasing, `peer_count` 0 | No seeds configured, seeds down, or the peer is banning you | Set `MINEAI_SEEDS=host:port`; check firewall for TCP 18081; look for `peer_banned` / `peer_rejected` in the log |
| `sync_status` stays `syncing` | Peer has more work but is slow/unreachable, or your chain diverged deeper than 100 blocks | Check peers; if you were offline on a different chain for over 100 blocks, discard the data and resync |
| Your mined blocks are rejected: `bad_timestamp` | Your clock is more than 5 minutes ahead | Fix the system clock (NTP) |
| Miner: `block REJECTED [bad_difficulty]` | The chain moved on; you mined an old template | Normal when another block arrives first; the miner retries. Persistent errors mean the node and miner disagree on the network |
| Miner: `refused to give a mining template [http_error]` (403) | Mining endpoints answer loopback clients only | Run the miner on the same machine as the node (`--node http://127.0.0.1:18080`) |
| Miner very slow | Difficulty is high for one CPU core | Expected: difficulty follows total hashrate. Add threads with `--threads` |
| Wallet: `wrong password or corrupted wallet file` | Wrong password, or a file altered/copied to a different address or network | Try again; restore from a backup with `wallet restore` |
| Wallet: `Refusing to overwrite existing file` | The target already exists | Choose a new file name. Existing wallets are never overwritten |
| Wallet: `This wallet is not a testnet wallet` | Wallet from another network | Wallets are bound to their network; use `--network` matching the wallet |
| `send`: `Insufficient available balance` right after mining | Mining rewards are immature for 10 blocks on testnet | Wait for maturity; `balance` shows the immature amount |
| `send`: transaction accepted but not confirmed | Nobody mined a block yet, or the fee is below what pending transactions pay | Wait or run the miner; the preview warns when your fee is low |
| API `413 too_large` | Request body over 64 KiB (block submission allows more) | Send a smaller request |
| API `429 rate_limited` | Too many requests from one address | Slow down or raise `MINEAI_RATE_LIMIT_PER_MINUTE` |
| `Address already in use` | Another process uses the port | Set `MINEAI_PORT` / `MINEAI_P2P_PORT` |
| Windows package: `install.ps1` refuses the target | The folder exists and is not a MineAI install | Pick another `-Target` |

## Collecting information for a bug report

`mineai version`, the network name, `Invoke-RestMethod http://127.0.0.1:18080/api/v1/status`, the last lines of the node log
(JSON lines; they never contain keys or passwords), and the output of `mineai check` (with the node stopped).
**Never send wallet files or passwords.**
