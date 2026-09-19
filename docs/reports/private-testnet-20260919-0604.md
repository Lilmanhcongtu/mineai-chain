# Private testnet report (Milestone 7)

- Generated: 2026-09-19 06:04:34 local time, run time 11.8 minutes
- Network: `privnet` (target block time 5 s, initial difficulty 16,384, LWMA window 30); five separate `mineai node` OS processes on 127.0.0.1
- Machine: Windows-10-10.0.26200-SP0, 24 CPUs, Python 3.10.0
- **Result: 60/60 checks passed**

## Run history: this is run 5 of 5

The program was run five times. Every failure was investigated, and four real defects were found and fixed before this run passed.
Runs 1-2 have full reports and runs 3-4 have short notes in this folder.

| Run | Result | What it found |
|---|---|---|
| 1 | 37/43 | Harness problems, plus one real finding: the miner noticed new blocks only every 3 s, wasting most of its work at 5 s blocks (extra miners added almost no hashrate). Fixed: 0.25 s polling of a new lightweight `/api/v1/tip` endpoint. |
| 2 | 52/57 | **Real bug:** a node that received a sender's transaction N+1 before N rejected it, marked it seen, never re-requested it and never relayed it (relay can reorder). Fixed with a bounded holding pool. The difficulty scenario's phases were also shorter than the 30-block difficulty window; an offline recomputation confirmed all 120 declared difficulties matched the algorithm exactly. |
| 3 | aborted | **Serious bug:** during a transaction burst honest nodes exceeded the per-peer message rate limit, dropped messages were scored, scores never decayed, and honest nodes **banned each other**, splitting the network. Fixed: higher limits, decaying scores, honest transaction races not scored, batched announcements, `get_data` no longer truncating batches. |
| 4 | aborted | **Real bug:** the API's 64 KiB request cap rejected any block above about 150 transactions (consensus allows 512 KiB), so miners stopped and a full mempool could never drain. Fixed with a per-endpoint limit for block submission. |
| 5 | **60/60** | This report. |

Each fix has regression tests (`tests/network/test_future_tx.py`, `test_burst_resilience.py`, `tests/integration/test_api.py`, `test_miner.py`).
A single passing run is evidence, not proof: timing-dependent behaviour should be re-checked by running the program repeatedly.

This is a single-machine test: all nodes share one clock and reach each other over loopback with no latency or packet loss. Partitions are emulated by disabling P2P on a node, not by dropping packets. See Known limitations.

## Scenarios

| Scenario | Checks | Time |
|---|---|---|
| setup | 3/3 | 11 s |
| baseline | 2/2 | 76 s |
| kill_recovery | 10/10 | 61 s |
| rolling_restart | 2/2 | 26 s |
| partition | 4/4 | 51 s |
| late_joiner | 2/2 | 12 s |
| tx_flood | 6/6 | 9 s |
| attack | 14/14 | 7 s |
| corruption | 5/5 | 3 s |
| difficulty | 6/6 | 446 s |
| final | 6/6 | 5 s |

## Every check

| Scenario | Check | Result | Detail |
|---|---|---|---|
| setup | all 5 nodes start and answer the health check | PASS | 5/5 ready |
| setup | all nodes share the pinned privnet genesis | PASS | 1 distinct |
| setup | seeded as a chain, discovery forms a mesh (every node has >= 3 peers) | PASS | {'n1': 4, 'n2': 3, 'n3': 4, 'n4': 4, 'n5': 3} |
| baseline | chain grows under two competing miners | PASS | height 23 |
| baseline | after mining stops, all 5 nodes agree on the same tip | PASS | height 23 |
| kill_recovery | kill #1: n4's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jysbt9wl\n4\privnet\chain.db verified: height 23, total work 98515024, minted |
| kill_recovery | kill #1: n4 restarts | PASS |  |
| kill_recovery | kill #1: n4 catches up to the live network (within 2 blocks) | PASS | 26 vs 26 |
| kill_recovery | kill #2: n2's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jysbt9wl\n2\privnet\chain.db verified: height 30, total work 147512807, minte |
| kill_recovery | kill #2: n2 restarts | PASS |  |
| kill_recovery | kill #2: n2 catches up to the live network (within 2 blocks) | PASS | 35 vs 35 |
| kill_recovery | kill #3: n5's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jysbt9wl\n5\privnet\chain.db verified: height 38, total work 198714480, minte |
| kill_recovery | kill #3: n5 restarts | PASS |  |
| kill_recovery | kill #3: n5 catches up to the live network (within 2 blocks) | PASS | 43 vs 43 |
| kill_recovery | after 3 crashes and restarts all nodes converge on one tip | PASS | height 43 |
| rolling_restart | after a rolling restart of 4 nodes under load, all nodes converge | PASS | height 51 |
| rolling_restart | the mesh re-forms from persisted peers (>= 2 peers each) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| partition | while partitioned the two sides really diverge | PASS | heights {'n1': 63, 'n2': 51, 'n3': 57, 'n4': 57, 'n5': 57} |
| partition | after healing, all 5 nodes converge on one chain | PASS | height 63 |
| partition | every node on the LIGHTER side reorganized (and the heavier side did not) | PASS | reorgs gained {'n1': 0, 'n2': 0, 'n3': 1, 'n4': 1, 'n5': 1}; lighter side ['n3', 'n4', 'n5'] |
| partition | the surviving chain is the one with the most cumulative work | PASS | final 511,766,859 vs max side 511,766,859 |
| late_joiner | a wiped node performs a full initial sync from peers | PASS | 66+ blocks in 0.6s |
| late_joiner | and ends on exactly the same tip as everyone else | PASS |  |
| tx_flood | the funding account has mined and matured enough to fund the flood (>= 400 MAI) | PASS | 1375.0 MAI available |
| tx_flood | 8 funding transactions confirm | PASS |  |
| tx_flood | every submitted transaction was accepted by the node it was sent to | PASS | 168 ok, 0 rejected |
| tx_flood | all transactions are mined and every mempool drains | PASS | {'n1': 0, 'n2': 0, 'n3': 0, 'n4': 0, 'n5': 0} |
| tx_flood | nodes agree on the chain after the flood | PASS |  |
| tx_flood | every account balance is identical on all 5 nodes and equals the expected value | PASS | [] |
| attack | the node greeted every attacker before rejecting it (each attack really reached the handshake) | PASS | all greeted |
| attack | attack 'wrong network': connection is dropped | PASS |  |
| attack | attack 'wrong genesis': connection is dropped | PASS |  |
| attack | attack 'random bytes': connection is dropped | PASS |  |
| attack | attack '50 MB length prefix': connection is dropped | PASS |  |
| attack | attack 'HTTP request on the P2P port': connection is dropped | PASS |  |
| attack | attack 'invalid-block spam #1': connection is dropped | PASS |  |
| attack | attack 'invalid-block spam #2': connection is dropped | PASS |  |
| attack | attack 'banned identity cannot reconnect': connection is dropped | PASS |  |
| attack | attack 'ping flood (3000 messages)': connection is dropped | PASS |  |
| attack | the abuse is visible in the target's metrics (penalty points and bans) | PASS | +650 points, +6 bans |
| attack | the target node is still healthy and serving the API | PASS |  |
| attack | the target keeps following the chain after the attack | PASS |  |
| attack | all nodes still agree after the attack | PASS |  |
| corruption | tampered balances are detected by `mineai check` | PASS | CHECK FAILED: sum of balances differs from minted supply: database corrupted |
| corruption | the node REFUSES to start on a tampered database | PASS | r("sum of balances differs from minted supply: database corrupted")
mineai.blockchain.ChainError: sum of balances differs from minted supply: database |
| corruption | physical corruption of the database file is detected | PASS | CHECK FAILED: database is corrupted or not a MineAI database: database disk image is malformed |
| corruption | the node refuses to start on a corrupted database file | PASS |  |
| corruption | after discarding the damaged database the node resyncs and converges | PASS | height 70 |
| difficulty | phase A: with a matching hashrate the block spacing is near the 5 s target (2.5 s - 12 s) | PASS | mean 4.41 s |
| difficulty | phase B: blocks speed up right after hashrate rises (first third faster than 70% of target) | PASS | 2 s |
| difficulty | phase B: difficulty then climbs (last-third median >= 1.3x phase A's) | PASS | 20,232,002 -> 57,161,677 |
| difficulty | phase B: block spacing recovers toward the target by the end (last third within 2 s - 12 s) | PASS | 3.27 s |
| difficulty | phase C: blocks slow down right after hashrate falls (first third slower than 1.5x target) | PASS | 9.83 s |
| difficulty | phase C: difficulty then falls (last-third median <= 0.9x phase B's) | PASS | 57,161,677 -> 34,982,829 |
| final | network converges at the end | PASS |  |
| final | offline full verification of n1's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jysbt9wl\n1\privnet\chain.db verified: height 153, total work 3618236237, min |
| final | offline full verification of n2's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jysbt9wl\n2\privnet\chain.db verified: height 153, total work 3618236237, min |
| final | offline full verification of n3's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jysbt9wl\n3\privnet\chain.db verified: height 153, total work 3618236237, min |
| final | offline full verification of n4's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jysbt9wl\n4\privnet\chain.db verified: height 153, total work 3618236237, min |
| final | offline full verification of n5's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jysbt9wl\n5\privnet\chain.db verified: height 153, total work 3618236237, min |

## Measurements

- Baseline block interval with 2 miners: mean 3.76 s, median 1 s (target 5 s); reorganizations seen by then: 2
- Initial sync of a wiped node: 66 blocks in 0.6 s
- Transaction flood: 168 transactions accepted across 5 nodes, all mined

Difficulty response to real hashrate changes (measured from the chain's block timestamps):

| Phase | Miners | Blocks | Mean interval | First third | Last third | Difficulty start | end | Last-third median |
|---|---|---|---|---|---|---|---|---|
| A: 3 miners | 3 | 28 | 4.41 s | 4.89 s | 3.89 s | 18,189,756 | 24,049,222 | 20,232,002 |
| B: 10 miners (3.3x hashrate) | 10 | 36 | 3.14 s | 2 s | 3.27 s | 25,760,623 | 67,471,062 | 57,161,677 |
| C: 3 miners again (0.3x hashrate) | 3 | 19 | 10 s | 9.83 s | 5 s | 68,398,611 | 37,826,146 | 34,982,829 |

## Final counters per node

| Node | Height | Blocks accepted | Blocks rejected | Reorgs | Blocks connected in reorgs | Peers banned | Penalty points | Tx accepted | Tx rejected |
|---|---|---|---|---|---|---|---|---|---|
| n1 | 153 | 90 | 0 | 0 | 0 | 0 | 0 | 168 | 0 |
| n2 | 153 | 102 | 3 | 0 | 0 | 6 | 650 | 168 | 0 |
| n3 | 153 | 153 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| n4 | 153 | 107 | 0 | 1 | 7 | 0 | 0 | 168 | 0 |
| n5 | 153 | 153 | 0 | 0 | 0 | 0 | 0 | 168 | 0 |

Counters restart from zero whenever a node is killed, so they cover only the last run of each process.

## Monitoring summary (1 Hz poll of every node)

- 666 snapshots; all live nodes on the same tip in 93% of them
- longest continuous disagreement: about 44 s (includes deliberate partitions and restarts)
- highest chain height seen: 153

## Timeline

- `     0s` [setup] begin
- `    11s` [setup] end (11s)
- `    11s` [baseline] begin
- `    11s` [baseline] 2 miners started on n1 and n3
- `    87s` [baseline] block interval mean 3.8s median 1.0s over 17 blocks
- `    87s` [baseline] reorganizations observed across nodes so far: 2
- `    87s` [baseline] end (76s)
- `    87s` [kill_recovery] begin
- `    93s` [kill_recovery] HARD-KILLED n4 at network height 23 (no graceful shutdown)
- `   113s` [kill_recovery] HARD-KILLED n2 at network height 29 (no graceful shutdown)
- `   132s` [kill_recovery] HARD-KILLED n5 at network height 38 (no graceful shutdown)
- `   148s` [kill_recovery] end (61s)
- `   148s` [rolling_restart] begin
- `   151s` [rolling_restart] restarted n2
- `   155s` [rolling_restart] restarted n3
- `   159s` [rolling_restart] restarted n4
- `   163s` [rolling_restart] restarted n5
- `   174s` [rolling_restart] end (26s)
- `   174s` [partition] begin
- `   176s` [partition] n1 and n2 isolated at height 51
- `   222s` [partition] before healing: n1 work 511,766,859 vs majority work 427,848,654 -> the isolated node is heavier
- `   225s` [partition] partition healed: n1 and n2 rejoined with P2P
- `   225s` [partition] final total work 511766859
- `   225s` [partition] end (51s)
- `   225s` [late_joiner] begin
- `   236s` [late_joiner] wiped n5's data directory (network height 66); starting it from nothing
- `   237s` [late_joiner] end (12s)
- `   237s` [tx_flood] begin
- `   237s` [tx_flood] funding transactions submitted; waiting for them to confirm
- `   240s` [tx_flood] submitted 160 transfers across 5 nodes (0 rejected at submission)
- `   246s` [tx_flood] end (9s)
- `   246s` [attack] begin
- `   253s` [attack] end (7s)
- `   253s` [corruption] begin
- `   256s` [corruption] end (3s)
- `   256s` [difficulty] begin
- `   377s` [difficulty] phase 'A: 3 miners': {'label': 'A: 3 miners', 'miners': 3, 'blocks': 28, 'seconds': 120, 'interval_mean': 4.41, 'interval_first_third': 4.89, 'interval_last_third': 3.89, 'difficulty_start': 18189756, 'difficulty_end': 24049222, 'difficulty_last_third_median': 20232002}
- `   501s` [difficulty] phase 'B: 10 miners (3.3x hashrate)': {'label': 'B: 10 miners (3.3x hashrate)', 'miners': 10, 'blocks': 36, 'seconds': 120, 'interval_mean': 3.14, 'interval_first_third': 2, 'interval_last_third': 3.27, 'difficulty_start': 25760623, 'difficulty_end': 67471062, 'difficulty_last_third_median': 57161677}
- `   702s` [difficulty] phase 'C: 3 miners again (0.3x hashrate)': {'label': 'C: 3 miners again (0.3x hashrate)', 'miners': 3, 'blocks': 19, 'seconds': 200, 'interval_mean': 10, 'interval_first_third': 9.83, 'interval_last_third': 5, 'difficulty_start': 68398611, 'difficulty_end': 37826146, 'difficulty_last_third_median': 34982829}
- `   702s` [difficulty] end (446s)
- `   702s` [final] begin
- `   707s` [final] end (5s)

## Known limitations of this test

- One machine, one clock, loopback networking: no latency, jitter, packet loss, or clock skew was exercised.
- Partitions are emulated by disabling a node's P2P layer, not by blocking traffic between live peers.
- Hashrate comes from single-threaded Python CPU miners; the absolute numbers say nothing about a real network.
- Five nodes, one operator. Nothing here tests independently operated nodes or hostile majority behaviour.
- Disk-full, out-of-memory, and power-loss (as opposed to process-kill) failures were not tested.
