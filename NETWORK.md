# MineAI Networks

MineAI keeps three completely separate network profiles. Selecting one is done with `MINEAI_NETWORK`
(or `--network` on the wallet and miner).

| Profile | Purpose | State |
|---|---|---|
| `devnet` | Local development on one or more machines/processes | Working: multi-node P2P with fork choice and reorganizations |
| `testnet` | Future public test network | Profile defined; **genesis not created, no nodes exist** |
| `mainnet` | — | **Disabled in code.** Not launched, not planned for launch without explicit authorization |

Separation guarantees (all covered by tests):

* different `network_id` — transactions and blocks signed/hashed for one network are invalid on another;
* different address prefix (`DMAI`, `TMAI`, `MAI`) — an address from one network is rejected by the others;
* different default API and P2P ports and data directories (`<data dir>/<network>/chain.db`);
* a database opened with the wrong network is refused; wallets record their network and are refused elsewhere.

## Current capabilities (Milestone 5)

* Multi-node peer-to-peer networking over TCP (`PROTOCOL.md` section 10): handshake with network/genesis/version checks, peer discovery and persistence, transaction and block relay with duplicate suppression, locator-based synchronization, recovery after disconnects, connection limits, per-peer rate limits, misbehavior scoring and temporary bans.
* **Fork choice by cumulative work** (section 8): the best chain is the valid chain with the most total work, not the most blocks. Competing blocks are kept as side chains; orphans are held (bounded) until their parent arrives; a heavier branch triggers an atomic reorganization with state rollback, mempool restoration and permanent rejection of invalid branches. Equal work never causes switching (first seen stays).
* Verified with three independent node processes (`scripts\demo_three_nodes.ps1`): shared genesis, transaction and block relay between nodes, a network partition, and a real reorganization in which a node abandons a private fork and converges on the heaviest chain.
* **Reorganization depth limit:** nodes refuse reorganizations deeper than 100 blocks (policy, section 8.5). A partition that outlasts this would split the network permanently and require a manual resync.
* **Dynamic difficulty (Milestone 5):** the difficulty is retargeted every block towards a 60-second average (LWMA over median-filtered timestamps, PROTOCOL.md 5.10). Difficulty is a numeric target, no longer "leading hex zeros". The first 5 blocks use the initial difficulty (65,536 on devnet); a much faster miner will see it double per block until it reaches equilibrium. Blocks may be stamped at most 5 minutes ahead of a node's clock, so keep clocks synchronized.
* Nothing is hosted publicly: there are no seed nodes and no public testnet.
* **Milestone 6:** wallet, miner, explorer and a Windows package (see README). The API is versioned under `/api/v1`.
* Planned next: private testnet (7), public testnet candidate (8).

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
