# MineAI Networks

MineAI keeps three completely separate network profiles. Selecting one is done with `MINEAI_NETWORK`
(or `--network` on the wallet and miner).

| Profile | Purpose | State |
|---|---|---|
| `devnet` | Local development on one or more machines/processes | Working: multi-node P2P (linear chain, see limitations) |
| `testnet` | Future public test network | Profile defined; **genesis not created, no nodes exist** |
| `mainnet` | — | **Disabled in code.** Not launched, not planned for launch without explicit authorization |

Separation guarantees (all covered by tests):

* different `network_id` — transactions and blocks signed/hashed for one network are invalid on another;
* different address prefix (`DMAI`, `TMAI`, `MAI`) — an address from one network is rejected by the others;
* different default API and P2P ports and data directories (`<data dir>/<network>/chain.db`);
* a database opened with the wrong network is refused; wallets record their network and are refused elsewhere.

## Current capabilities (Milestone 3)

* Multi-node peer-to-peer networking over TCP (`PROTOCOL.md` section 10): handshake with network/genesis/version checks, peer discovery and persistence, transaction and block relay with duplicate suppression, initial and incremental synchronization, recovery after disconnects, connection limits, per-peer rate limits, misbehavior scoring and temporary bans.
* Verified with three independent node processes (`scripts\demo_three_nodes.ps1`): shared genesis, a transaction relayed between nodes, blocks mined on different nodes, identical chain tip everywhere.
* **Known limitation: no fork choice yet.** Chains are linear. If two nodes mine different blocks at the same height they will *not* converge; the later block is ignored. Run one miner at a time until Milestone 4 (cumulative-work selection and reorganizations).
* Difficulty is still fixed (Milestone 5). Nothing is hosted publicly: there are no seed nodes and no public testnet.
* Planned next: fork choice and reorgs (4), dynamic difficulty (5), wallet/miner/explorer (6), private then public testnet (7–8).

## Running a node

```powershell
.\scripts\setup_windows.ps1      # creates .venv, installs, verifies imports (stops on any failure)
.\scripts\start_node.ps1         # devnet on http://127.0.0.1:8080
```

### Running several nodes on one machine

Give every node its own data directory, API port, P2P port and (except the first) a seed:

```powershell
.\scripts\demo_three_nodes.ps1      # starts node1/node2/node3, mines, relays a transaction, checks the tips match, cleans up
```

Manually, per node: `MINEAI_DATA_DIR`, `MINEAI_PORT` (API), `MINEAI_P2P_PORT`, `MINEAI_SEEDS=host:port[,host:port]`.
P2P listens on `127.0.0.1` by default (`MINEAI_P2P_HOST`); binding elsewhere exposes an unaudited network service, so don't.

Keep the default `MINEAI_HOST=127.0.0.1`. If you bind elsewhere, the read API is exposed
(rate- and size-limited, but unauthenticated) and the mining endpoints stay restricted to loopback clients.
Do not put a reverse proxy in front without understanding that it changes the client address the loopback check sees.

## Testnet warning (for the future)

Testnet wallets and coins will be labelled `TESTNET` everywhere, carry no value, and may be reset at any time.
Never reuse a real password for a wallet on any MineAI network.
