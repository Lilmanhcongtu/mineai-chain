# Private testnet (Milestone 7)

A private, multi-node test network for exercising MineAI under failure. It uses the **`privnet`** profile:
network id `mineai-privnet-v1`, address prefix `PMAI`, ports 38080/38081, a **5-second** target block time and low
initial difficulty (16 384) so forks, reorganizations and difficulty changes happen in minutes. It is not the public
testnet (Milestone 8) and must never be exposed publicly. Coins have no value.

## Run the test program

```powershell
.\.venv\Scripts\python.exe scripts\private_testnet.py run                # ~12 minutes, writes docs\reports\private-testnet-<time>.md
.\.venv\Scripts\python.exe scripts\private_testnet.py run --quick        # shorter timings (smoke run)
.\.venv\Scripts\python.exe scripts\private_testnet.py run --only baseline,attack     # setup and final always run
.\.venv\Scripts\python.exe scripts\private_testnet.py monitor            # live table of a running private network
```

The harness starts five `mineai node` processes (`n1`..`n5`, API ports 38100-38104, P2P ports 38200-38204), seeded as a
*chain* (each node knows only its predecessor) so that peer discovery has to build the mesh. Mining and transaction test
keys exist only in memory and are never written to disk. Everything it starts is killed when it finishes.

| Scenario | What is done to the network | What must hold |
|---|---|---|
| setup | five processes start; seeded as a chain | shared genesis; discovery builds a mesh (every node has >= 3 peers) |
| baseline | two competing CPU miners on different nodes | chain grows; natural forks resolve; all tips equal once mining stops |
| kill_recovery | three **hard kills** (TerminateProcess, no shutdown hook) at random moments under load | database consistent immediately after each crash; node restarts, catches up, network converges |
| rolling_restart | four nodes restarted one after another under load | mesh re-forms from persisted peers; all converge |
| partition | two nodes cut off from P2P while both sides mine, then rejoin | the sides really diverge; after healing all converge on the heaviest chain via a reorganization |
| late_joiner | a node's data directory is wiped and it starts from nothing | full initial sync; identical tip |
| tx_flood | 8 accounts send ~170 transactions (each account through one node, like a real wallet; different accounts through different nodes) | all accepted, all mined, every mempool drains, balances identical on all nodes and equal to the expected values |
| attack | wrong network/genesis, random bytes, 50 MB length prefix, HTTP on the P2P port, invalid-block spam, a ban-evasion attempt, a 3000-message ping flood | every connection dropped; penalties and bans visible in metrics; node keeps serving and following the chain |
| corruption | a balance is edited behind the node's back; then the database file is physically damaged | `mineai check` detects both; the node refuses to start; discarding the data and resyncing recovers |
| difficulty | 3 miners, then 10 (3.3x hashrate), then 3 again, in phases long enough to cover the 30-block difficulty window | blocks speed up right after hashrate rises then difficulty climbs and spacing recovers; blocks slow after it falls then difficulty drops; measured from the chain's own timestamps |
| final | every node stopped | `mineai check` fully re-verifies each database (hashes, difficulty, work, balances, supply) |

## Monitoring

Every node exposes read-only metrics (no authentication, nothing sensitive):

* `GET /metrics` — Prometheus text format (`mineai_chain_height`, `mineai_chain_total_work`, `mineai_difficulty`,
  `mineai_estimated_hashrate`, `mineai_mempool_transactions`, `mineai_peers`, `mineai_side_blocks`, `mineai_orphan_blocks`,
  `mineai_synced`, `mineai_uptime_seconds`, and counters such as `mineai_blocks_accepted_total`,
  `mineai_blocks_rejected_total{code=...}`, `mineai_reorgs_total`, `mineai_transactions_rejected_total{code=...}`,
  `mineai_p2p_messages_received_total{type=...}`, `mineai_p2p_protocol_errors_total{reason=...}`,
  `mineai_p2p_peers_banned_total`, `mineai_p2p_penalty_points_total`).
* `GET /api/v1/metrics` — the same data as JSON. `GET /api/v1/status` — human-oriented summary.

Label values come from closed sets in the code, so hostile traffic cannot create unbounded time series.
Counters restart from zero when a process restarts. Point Prometheus/Grafana at `/metrics` if you want dashboards; none is shipped.

Useful alerts for a real deployment: peers == 0 for more than a minute; `mineai_synced` == 0 for more than a few minutes;
height not increasing for 10x the target block time; `mineai_reorgs_total` increasing faster than about one per hour;
any increase in `mineai_p2p_peers_banned_total` from an unknown source; `mineai_blocks_rejected_total{code="bad_pow"}` rising.

## Operator runbook

**A node crashed or was killed.** Just start it again. Blocks, account state and the mempool are committed in one
SQLite transaction, so it restarts at its last committed block and syncs the rest from peers. To be sure, stop it and run
`mineai check --network privnet --data-dir <dir>`; it prints `OK` and the height or `CHECK FAILED` with the reason.

**The node refuses to start with "database is corrupted" / "sum of balances differs" / "tip block hash does not verify".**
The data is damaged or was modified. Do not edit it. Move it aside (`chain.db*`), start the node with the same seeds and let
it resync from peers. Wallets are separate files and are not affected.

**A node is stuck behind** (`mineai_synced` 0, height not moving). Check `peers` (should be >= 2) and the node log for
`peer_rejected` / `peer_banned`. If it has no peers, its seeds are down or it is banned; restart it with correct `MINEAI_SEEDS`.
A node refuses reorganizations deeper than 100 blocks: if it was offline longer than that on a different chain, discard its
data and resync.

**A network partition healed and blocks were reorganized.** Expected. The side with less cumulative work reorganizes, its
mined rewards on the abandoned branch disappear, and transactions that were only on that branch return to the mempool.
Tell miners to wait for confirmations (block rewards are also immature for a few blocks).

**Suspected attacker on the P2P port.** `mineai_p2p_peers_banned_total` and `p2p_protocol_errors` show it. Bans last 10 minutes
per identity. P2P is unencrypted and unauthenticated by design at this stage; keep it on a private network or loopback.

**Clock problems.** A block stamped more than 5 minutes ahead of a node's clock is rejected until the clock catches up;
keep clocks synchronized (NTP).

## Results and what the test program found

The full program was run five times during Milestone 7 (`docs/reports/`). The fifth run passed all 60 checks in about 12 minutes.
The earlier runs are the more valuable record: they found four real defects that unit tests and in-process network tests had not.

1. **Miner stale-work polling** (every 3 s) wasted most hashing at 5 s blocks. Now 0.25 s against a cheap `/api/v1/tip` endpoint.
2. **Out-of-order transactions were lost.** A node that received nonce N+1 before N dropped it forever. Now held in a bounded pool and released in nonce order.
3. **Honest nodes banned each other under a transaction burst.** Message-rate limits were too tight, scores never decayed, and honest races were scored. Now: 200 msg/s with a burst of 1000, scores decay by one point per 5 s, honest races cost nothing, transaction announcements are batched.
4. **Blocks above about 150 transactions could not be submitted** because of the 64 KiB API body cap. Now block submission has its own limit (twice the consensus maximum).

Lesson for operators and for future milestones: exercise the real multi-process network under bursty load and hard failures, not only in-process tests.
