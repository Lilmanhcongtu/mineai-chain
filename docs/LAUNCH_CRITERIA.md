# Public testnet launch criteria — status

This release is a **candidate**. The launch itself requires infrastructure and time that only the project owner can provide,
and no paid infrastructure was created or is required to publish this candidate. **Nothing here has been launched.**

| Criterion | Status | Detail |
|---|---|---|
| A unique network id, separate ports, fixed genesis, separate database directory | **MET** | `mineai-testnet-v1`, ports 18080/18081, genesis pinned, `<data dir>/testnet/` |
| Separate wallet warning; testnet address separation; clear TESTNET labels | **MET** | `TMAI` addresses, banners on node/wallet/miner, explorer badge, API `label` |
| No transfer of V0.1 balances; no monetary value | **MET** | incompatible network ids/prefixes/genesis; label and warnings say so |
| Releases: Windows package, source archive, checksums, release notes, install and troubleshooting guides | **MET** | see `RELEASE_NOTES.md`, `INSTALL.md`, `TROUBLESHOOTING.md`; unsigned |
| Linux releases | **PARTIAL** | source install and the documented walkthrough pass in CI on Ubuntu (tests on Python 3.10/3.12); no Linux binaries or release artifacts, other distributions and macOS untried, systemd unit and VPS guide never run on a real host |
| At least five nodes | **MET (privately)** | five processes on one machine in the private test network |
| At least three independently hosted nodes | **NOT MET** | requires three operators/hosts; none exist |
| Nodes remain synchronized | **PARTIAL** | shown for minutes to hours on one machine, not on independent hosts |
| Nodes recover after disconnecting | **MET (privately)** | hard kills, rolling restarts, wiped-node resync |
| Forks resolve correctly | **MET (privately)** | partition/heal and racing miners; 30-seed convergence property |
| Difficulty behaves as designed | **PARTIAL** | simulations and a live 5-process run; not a real network |
| Wallet backup and restore work | **MET** | tests and the packaged end-to-end run |
| No unresolved critical security issue | **UNVERIFIED** | none known to the author; no audit; see `SECURITY_CHECKLIST.md` |
| The testnet operates for at least 60-90 days | **NOT MET** | not launched |

## What launching would additionally need (owner decisions and actions)

1. Host at least three seed/full nodes on independent machines and publish their addresses (`MINEAI_SEEDS`).
2. Decide the public announcement wording; keep the "no monetary value / may be reset" statement prominent.
3. Define the vulnerability-reporting contact and an incident/reset process (`SECURITY_CHECKLIST.md`, OPEN items).
4. Decide on release signing (a maintainer-held key with a published public key, or a code-signing certificate).
5. Linux is verified in CI on Ubuntu (source install). Still open: run it on a real host (`DEPLOY_VPS.md`), and either verify macOS/other distributions or state clearly which platforms are supported.
6. Run the private test program repeatedly and a longer soak before announcing.
7. Decide, before the testnet is public, whether the testnet keeps the current emission rules (it does in this candidate).

## Mainnet

Not part of this release and **disabled in the software**. A separate readiness checklist (independent audit, new genesis
and network id, signed and reproducible releases, several independent operators, incident response, legal review, and an
explicit decision on the emission schedule) comes later, and no mainnet will be launched without explicit authorization.
