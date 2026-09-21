# Security checklist — public testnet candidate (0.2.0rc1)

Legend: **DONE** = implemented and covered by an automated test or a recorded run (evidence given); **PARTIAL** = some of it;
**OPEN** = not done. "DONE" means *tested in this environment*, not *audited*. **No independent security review has taken
place; this checklist is a self-assessment.**

## Consensus and protocol

| Item | Status | Evidence |
|---|---|---|
| Integer-only money, bounded integers, no floats | DONE | `tests/unit/test_encoding.py`, `tests/adversarial/test_attacks.py` |
| Deterministic encodings pinned by golden vectors and an independent re-implementation | DONE | `test_encoding.py` |
| Signatures/txids/block hashes bound to the network id (no cross-network replay) | DONE | `tests/consensus/test_rules.py` |
| Per-network address prefixes; canonical-only addresses (no aliases) | DONE | `test_encoding.py`, `test_profiles.py` |
| Nonce-based replay/double-spend protection | DONE | `test_rules.py` |
| Supply cap enforced, fees never mint, `sum(balances) == minted` checked on open | DONE | `test_rules.py`, `test_persistence.py` |
| Timestamp rules (median-time-past, +5 min), difficulty validated per branch | DONE | `test_rules.py`, `test_difficulty.py` |
| Cumulative-work fork choice; atomic reorganization with full rollback on any invalid block | DONE | `test_forks.py` (30-seed convergence property), private testnet partition scenario |
| Consensus rules of the testnet frozen and pinned (genesis + parameter fingerprint) | DONE | `tests/unit/test_release_readiness.py` |
| Difficulty algorithm behaviour under hashrate steps, pauses, oscillation, timestamp attacks | PARTIAL | simulations + one live 5-process run; **never on a real network** |
| Formal specification reviewed by someone else | OPEN | `PROTOCOL.md` exists; no external review |

## Networking

| Item | Status | Evidence |
|---|---|---|
| Length checked before body; strict allow-list message schemas; malformed input drops the peer | DONE | `tests/network/test_p2p.py` (20 malformed vectors) |
| Handshake with network, genesis and version checks | DONE | `test_p2p.py` |
| Rate limits, decaying misbehavior scores, temporary bans; honest bursts not punished | DONE | `test_burst_resilience.py`; private testnet run 5 |
| Duplicate-request suppression, unsolicited-response rejection | DONE | `test_p2p.py` |
| Bounded holding pools (orphan blocks, future-nonce transactions), bounded side blocks | DONE | `test_forks.py`, `test_future_tx.py` |
| Survives hostile connections on a live node | DONE | private testnet `attack` scenario (9 attack types) |
| **Transport encryption and peer authentication** | **OPEN** | plaintext, self-declared node ids |
| Eclipse-attack resistance (address diversity, anchor peers) | OPEN | |
| Behaviour under latency, loss, NAT | OPEN | untested |

## API and explorer

| Item | Status | Evidence |
|---|---|---|
| Strict input validation, uniform errors, no stack traces | DONE | `test_api.py` |
| Body-size limits (block submission allowed its own larger limit) and rate limiting | DONE | `test_api.py` |
| Mining endpoints loopback-only; no admin/wallet endpoints | DONE | `test_api.py`, `test_explorer.py` |
| Explorer read-only, escaped output, no scripts, no keys | DONE | `test_explorer.py` |
| Metrics contain no sensitive data; bounded label cardinality | DONE | `test_metrics.py` |
| Authentication for the API / metrics | OPEN | intended for loopback/private networks only |

## Wallet

| Item | Status | Evidence |
|---|---|---|
| Keys from the OS CSPRNG; scrypt (N=2^17) + AES-256-GCM bound to address and network | DONE | `test_wallet_and_logging.py` |
| No password on the command line; never printed or logged | DONE | `test_wallet_features.py`, `test_wallet_and_logging.py` |
| Locking and auto-lock (expired sessions cannot be revived) | DONE | `test_wallet_features.py` |
| Verified backup/restore; atomic password change; never overwrites | DONE | `test_wallet_features.py`, packaged end-to-end run |
| Strict plain-decimal amounts; transfer preview and confirmation | DONE | `test_wallet_features.py` |
| Recovery phrase (BIP-39), hardware wallet support | OPEN | not implemented |
| Secure memory wiping | OPEN | not possible in Python; best effort only |

## Data and recovery

| Item | Status | Evidence |
|---|---|---|
| Atomic block + state + mempool commits; crash consistency | DONE | `test_persistence.py`; three hard kills in the private testnet |
| Integrity checks on open; offline full verification (`mineai check`) | DONE | private testnet `corruption` scenario |
| Forward-only migrations; foreign/legacy/newer databases refused | DONE | `test_persistence.py` |

## Release engineering and operations

| Item | Status | Notes |
|---|---|---|
| Checksums published for every artifact | DONE | `SHA256SUMS.txt` |
| **Signed releases** | **OPEN** | no signing key/certificate; needs a maintainer-held key and a published public key |
| **Reproducible / verifiable builds** | **OPEN** | |
| **Linux build and test** | **PARTIAL** | full test suite and the `install.sh` walkthrough pass in CI on Ubuntu (Python 3.10/3.12), `.github/workflows/ci.yml`; other distributions, macOS, a real host and the systemd unit not tried |
| Continuous integration | PARTIAL | GitHub Actions runs the tests on Ubuntu and Windows (Python 3.10/3.12) and a Linux install walkthrough on every push (`.github/workflows/ci.yml`); no dependency scanning, no release automation, no signing |
| Dependency pinning / vulnerability scanning (`pip-audit`) | OPEN | version ranges only |
| **Vulnerability reporting address and response process** | **PARTIAL** | address chosen: security@mineai.dev. A DNS check on 2026-09-21 found no MX records for mineai.dev, so the mailbox cannot receive mail yet. A response and incident process is drafted in `VULNERABILITY_RESPONSE.md`, but its timelines are unconfirmed proposals. The mailbox and the confirmed process must both be done before any public launch |
| Incident-response and rollback/reset process | OPEN | runbook covers node-level recovery only |
| **Independent security audit** | **OPEN** | |
| Legal / compliance review of the public testnet | OPEN | not done; not legal advice |

## Result

No **critical** defect is known to the author at the time of writing, but that statement is only as strong as the testing
described above; the private testnet found four real availability/correctness bugs after the unit tests were already green,
so more should be expected. Items marked **OPEN** in bold should be resolved before any public announcement.
