# Known limitations (0.2.0rc1)

An honest list of what this release does **not** do or has **not** been shown to do. Nothing here is hidden elsewhere.

## Not verified

| Area | Status |
|---|---|
| Linux | **Verified only on GitHub Actions Ubuntu runners** (`ubuntu-latest`, Python 3.10 and 3.12): the full test suite passes there, and `packaging/linux/install.sh` plus the documented walkthrough (install, node, wallet, mine, balance, offline `mineai check`) runs end to end on every commit. **Not verified:** other distributions, macOS, a real long-running host, the systemd unit (`packaging/linux/mineai-node.service`) and `DEPLOY_VPS.md`. No Linux binaries are provided (source install only). |
| Real networks | Only tested on one machine over loopback (private five-process network, plus in-process tests). No latency, jitter, packet loss, NAT, IPv6 or clock skew. |
| Independent operators | No third-party node, miner or wallet user has run this. |
| Long duration | The longest continuous run is minutes to hours, not the 60-90 days a public testnet needs. |
| Failure modes | Process kills were tested; power loss, disk full, out-of-memory and filesystem faults were not. |
| Adversaries | Malformed and abusive P2P traffic from one attacker was tested. Hostile-majority hashrate, eclipse attacks, timestamp attacks by a large miner on a real network and coordinated multi-peer attacks were not. |
| Security review | **No independent audit.** No formal verification. |

## Design limitations

* **P2P is unencrypted and unauthenticated.** Traffic is plaintext; node ids are self-declared; a peer can lie about its height/work; an on-path attacker can read or drop messages. No eclipse-attack protection (no address diversity, no anchor connections). Bans can be evaded by generating new node ids (non-loopback IPs are also banned).
* **Abuse handling is tuned by observation.** Rate limits, ban thresholds and score decay were wrong twice in ways only a live test exposed (both fixed); they may need retuning on real networks.
* **Reorganizations deeper than 100 blocks are refused.** A partition longer than that splits the network until nodes discard their data and resync.
* **No emergency difficulty adjustment.** A large drop in hashrate slows blocks until the 30-60 block difficulty window adapts (measured: about 10 s blocks for a few dozen blocks after a 70% drop on the private network; on the 60 s testnet the same drop would mean minutes per block).
* **Timestamp tolerance is 5 minutes.** Nodes with a clock more than 5 minutes fast get their blocks rejected. A near-majority attacker can slow blocks by about 1.5x by back-dating timestamps (simulated).
* **Mempool:** no replace-by-fee and no eviction by fee rate (new transactions are refused when full); at most 25 pending transactions per sender; future-nonce holding pool is small (100 transactions).
* **Proof of work is SHA-256 on CPUs.** It is not ASIC- or GPU-resistant. RandomX was evaluated (`RANDOMX_EVALUATION.md`) and is **not** implemented.
* **Single-machine miner hashrate** is modest (Python and `hashlib`); the difficulty numbers say nothing about a real network.
* **Emission schedule:** 25 MAI per block with no halving; the cap is reached in about 7.6 years, after which miners earn fees only. This is unchanged for the testnet; the schedule for any future mainnet is an **open decision**.
* **Initial testnet difficulty (65,536) is a guess** for early hardware. The algorithm corrects it (at most 2x per block), but the first blocks may be very fast.

## Wallet

* No recovery phrase (BIP-39 not implemented; a custom format will never be invented). The encrypted file plus its password is the only backup.
* No hardware-wallet support.
* While unlocked the key lives in Python process memory and cannot be securely wiped; lock is best effort. Malware or a keylogger defeats everything.
* Owner-only file permissions are best effort on Windows (`icacls`).
* Password strength is only a floor (>= 10 characters, not repetitive, not in a short common list).

## Operations and release engineering

* **Artifacts are unsigned** (no code-signing certificate, no signing key); SmartScreen/antivirus may warn.
* **Builds are not reproducible**; the Windows package was built on the developer's machine.
* **Continuous integration is basic**: GitHub Actions runs the tests on Ubuntu and Windows (Python 3.10/3.12) and a Linux install walkthrough on every push. There is no dependency scanning, no release automation and no signing.
* **No public seed nodes, no hosted infrastructure, no status page, no incident-response process yet.**
* **Vulnerability reporting contact is not yet defined** (`SECURITY.md` says so).
* Metrics endpoints are unauthenticated (they reveal height, peers, uptime and abuse counters, nothing sensitive).
* No API authentication; the HTTP API is meant for loopback or a private network. Mining endpoints answer loopback clients only, which a reverse proxy would defeat.

## Explicitly out of scope for this release

Smart contracts, token sales, exchanges, custody, mining pools, mobile wallets, governance, an AI marketplace, and any mainnet.
