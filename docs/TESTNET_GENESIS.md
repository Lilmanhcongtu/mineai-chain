# Testnet genesis (0.2.0rc1)

| Field | Value |
|---|---|
| Network id | `mineai-testnet-v1` |
| Genesis hash | `2ddd2b532e7d313ab4fed3c7ddbba84297d9f35fd040292e2046da471d29d335` |
| Height | 0 |
| Timestamp | 1767225600 (2026-01-01T00:00:00Z) |
| `previous_hash`, `merkle_root` | 64 zeros |
| Difficulty / nonce | 0 / 0 (the genesis block has no proof of work) |
| Transactions | none: **no premine, no coinbase, no allocation** |
| Address prefix | `TMAI` |
| Initial difficulty / minimum | 65,536 / 256 (retargeted every block afterwards) |
| Target block time | 60 s |
| Subsidy / maximum supply | 25 MAI / 100,000,000 MAI |
| Coinbase maturity | 10 blocks |

The genesis block is a pure function of the network parameters (`PROTOCOL.md` section 5.6). Compute or verify it yourself:

```powershell
.\.venv\Scripts\python.exe -c "from mineai.config import TESTNET; from mineai import consensus as C; print(C.genesis_block(TESTNET)['hash'])"
```

A running node reports it at `GET /api/v1/status` (`genesis_hash`) and refuses any peer or database with a different genesis.

## Frozen

The consensus rules of `mineai-testnet-v1` are pinned by `consensus_fingerprint` (a hash of every consensus-relevant
parameter) in `tests/unit/test_release_readiness.py`. **Changing any consensus rule requires a new network id, a new genesis
and a testnet reset.** The genesis timestamp is in the past, so mining can start as soon as nodes are running; nothing is
gated on a launch date.

## Not the same as mainnet

Testnet coins have no monetary value and are unrelated to any future mainnet. No testnet, devnet or V0.1 balance will ever carry over.
