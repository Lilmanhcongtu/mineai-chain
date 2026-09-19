> **Run 1 of 2 (kept for the record).** 37/43 checks passed. All 6 failures were problems in the test harness or in the
> miner's stale-work polling, not in the network: the flood scenario demanded funds two miners could not equally earn, the attack
> scenario treated "the node already dropped me" as a crash, and the difficulty scenario assumed six miners produce six times the
> hashrate (they did not: the miner noticed new blocks only every 3 s, wasting most work at 5 s blocks). All were fixed and the
> program was re-run; see the later report.

# Private testnet report (Milestone 7)

- Generated: 2026-09-19 05:12:31 local time, run time 12.2 minutes
- Network: `privnet` (target block time 5 s, initial difficulty 16,384, LWMA window 30); five separate `mineai node` OS processes on 127.0.0.1
- Machine: Windows-10-10.0.26200-SP0, 24 CPUs, Python 3.10.0
- **Result: 37/43 checks passed, 6 FAILED**

This is a single-machine test: all nodes share one clock and reach each other over loopback with no latency or packet loss. Partitions are emulated by disabling P2P on a node, not by dropping packets. See Known limitations.

## Scenarios

| Scenario | Checks | Time |
|---|---|---|
| setup | 3/3 | 11 s |
| baseline | 2/2 | 75 s |
| kill_recovery | 10/10 | 60 s |
| rolling_restart | 2/2 | 26 s |
| partition | 3/3 | 50 s |
| late_joiner | 2/2 | 11 s |
| tx_flood | 3/6 | 255 s |
| attack | 0/1 | 1 s |
| corruption | 5/5 | 3 s |
| difficulty | 1/3 | 232 s |
| final | 6/6 | 5 s |

## Every check

| Scenario | Check | Result | Detail |
|---|---|---|---|
| setup | all 5 nodes start and answer the health check | PASS | 5/5 ready |
| setup | all nodes share the pinned privnet genesis | PASS | 1 distinct |
| setup | seeded as a chain, discovery forms a mesh (every node has >= 3 peers) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| baseline | chain grows under two competing miners | PASS | height 28 |
| baseline | after mining stops, all 5 nodes agree on the same tip | PASS | height 28 |
| kill_recovery | kill #1: n4's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-tc31ugna\n4\privnet\chain.db verified: height 31, total work 139801170, minte |
| kill_recovery | kill #1: n4 restarts | PASS |  |
| kill_recovery | kill #1: n4 catches up to the live network (within 2 blocks) | PASS | 33 vs 33 |
| kill_recovery | kill #2: n2's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-tc31ugna\n2\privnet\chain.db verified: height 33, total work 154927605, minte |
| kill_recovery | kill #2: n2 restarts | PASS |  |
| kill_recovery | kill #2: n2 catches up to the live network (within 2 blocks) | PASS | 33 vs 33 |
| kill_recovery | kill #3: n5's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-tc31ugna\n5\privnet\chain.db verified: height 34, total work 163259495, minte |
| kill_recovery | kill #3: n5 restarts | PASS |  |
| kill_recovery | kill #3: n5 catches up to the live network (within 2 blocks) | PASS | 34 vs 34 |
| kill_recovery | after 3 crashes and restarts all nodes converge on one tip | PASS | height 34 |
| rolling_restart | after a rolling restart of 4 nodes under load, all nodes converge | PASS | height 37 |
| rolling_restart | the mesh re-forms from persisted peers (>= 2 peers each) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| partition | while partitioned the two sides really diverge | PASS | heights {'n1': 54, 'n2': 37, 'n3': 47, 'n4': 47, 'n5': 47} |
| partition | after healing, all 5 nodes converge on one chain | PASS | height 54 |
| partition | the losing side performed a chain reorganization | PASS | reorg counters {'n1': 0, 'n2': 0, 'n3': 1, 'n4': 1, 'n5': 1} |
| late_joiner | a wiped node performs a full initial sync from peers | PASS | 56+ blocks in 0.6s |
| late_joiner | and ends on exactly the same tip as everyone else | PASS |  |
| tx_flood | miner accounts have matured rewards to spend | **FAIL** |  |
| tx_flood | 8 funding transactions confirm | **FAIL** |  |
| tx_flood | every submitted transaction was accepted by the node it was sent to | **FAIL** | 39 ok, 129 rejected |
| tx_flood | all transactions are mined and every mempool drains | PASS | {'n1': 0, 'n2': 0, 'n3': 0, 'n4': 0, 'n5': 0} |
| tx_flood | nodes agree on the chain after the flood | PASS |  |
| tx_flood | every account balance is identical on all 5 nodes and equals the expected value | PASS | [] |
| attack | scenario completed without an unexpected error | **FAIL** | ConnectionAbortedError: [WinError 10053] An established connection was aborted by the software in your host machine |
| corruption | tampered balances are detected by `mineai check` | PASS | CHECK FAILED: sum of balances differs from minted supply: database corrupted |
| corruption | the node REFUSES to start on a tampered database | PASS | r("sum of balances differs from minted supply: database corrupted")
mineai.blockchain.ChainError: sum of balances differs from minted supply: database |
| corruption | physical corruption of the database file is detected | PASS | CHECK FAILED: database is corrupted or not a MineAI database: database disk image is malformed |
| corruption | the node refuses to start on a corrupted database file | PASS |  |
| corruption | after discarding the damaged database the node resyncs and converges | PASS | height 102 |
| difficulty | difficulty rises when hashrate rises 6x | **FAIL** | 9177396 -> 8173351 |
| difficulty | difficulty falls again when hashrate drops | **FAIL** | 8173351 -> 8547277 |
| difficulty | block spacing settles near the 5 s target in every phase (2.5 s - 12 s) | PASS | [4.71, 5.75, 5.11] |
| final | network converges at the end | PASS |  |
| final | offline full verification of n1's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-tc31ugna\n1\privnet\chain.db verified: height 150, total work 1112479772, min |
| final | offline full verification of n2's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-tc31ugna\n2\privnet\chain.db verified: height 150, total work 1112479772, min |
| final | offline full verification of n3's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-tc31ugna\n3\privnet\chain.db verified: height 150, total work 1112479772, min |
| final | offline full verification of n4's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-tc31ugna\n4\privnet\chain.db verified: height 150, total work 1112479772, min |
| final | offline full verification of n5's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-tc31ugna\n5\privnet\chain.db verified: height 150, total work 1112479772, min |

## Failures

- **tx_flood: miner accounts have matured rewards to spend** — 
- **tx_flood: 8 funding transactions confirm** — 
- **tx_flood: every submitted transaction was accepted by the node it was sent to** — 39 ok, 129 rejected
- **attack: scenario completed without an unexpected error** — ConnectionAbortedError: [WinError 10053] An established connection was aborted by the software in your host machine
- **difficulty: difficulty rises when hashrate rises 6x** — 9177396 -> 8173351
- **difficulty: difficulty falls again when hashrate drops** — 8173351 -> 8547277

## Measurements

- Baseline block interval with 2 miners: mean 3.09 s, median 1.0 s (target 5 s); reorganizations seen by then: 0
- Initial sync of a wiped node: 56 blocks in 0.6 s
- Transaction flood: 39 transactions accepted across 5 nodes, all mined

Difficulty response to a real hashrate change:

| Phase | Miners | Blocks | Mean interval | Mean interval, 2nd half | Difficulty at start | at end |
|---|---|---|---|---|---|---|
| 1 miner | 1 | 14 | 4 s | 4.71 s | 7,370,632 | 9,177,396 |
| 6 miners (6x hashrate) | 6 | 16 | 4.6 s | 5.75 s | 8,023,769 | 8,173,351 |
| 1 miner again (1/6 hashrate) | 1 | 18 | 4.82 s | 5.11 s | 7,772,388 | 8,547,277 |

## Final counters per node

| Node | Height | Blocks accepted | Blocks rejected | Reorgs | Blocks connected in reorgs | Peers banned | Penalty points | Tx accepted | Tx rejected |
|---|---|---|---|---|---|---|---|---|---|
| n1 | 150 | 96 | 2 | 0 | 0 | 0 | 0 | 39 | 19 |
| n2 | 150 | 113 | 3 | 0 | 0 | 6 | 650 | 39 | 22 |
| n3 | 150 | 150 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| n4 | 150 | 115 | 1 | 1 | 10 | 0 | 0 | 39 | 24 |
| n5 | 150 | 150 | 1 | 0 | 0 | 0 | 0 | 39 | 36 |

Counters restart from zero whenever a node is killed, so they cover only the last run of each process.

## Monitoring summary (1 Hz poll of every node)

- 676 snapshots; all live nodes on the same tip in 93% of them
- longest continuous disagreement: about 44 s (includes deliberate partitions and restarts)
- highest chain height seen: 150

## Timeline

- `     0s` [setup] begin
- `    11s` [setup] end (11s)
- `    11s` [baseline] begin
- `    11s` [baseline] 2 miners started on n1 and n3
- `    86s` [baseline] block interval mean 3.1s median 1.0s over 22 blocks
- `    86s` [baseline] reorganizations observed across nodes so far: 0
- `    86s` [baseline] end (75s)
- `    86s` [kill_recovery] begin
- `    92s` [kill_recovery] HARD-KILLED n4 at network height 31 (no graceful shutdown)
- `   112s` [kill_recovery] HARD-KILLED n2 at network height 33 (no graceful shutdown)
- `   131s` [kill_recovery] HARD-KILLED n5 at network height 34 (no graceful shutdown)
- `   146s` [kill_recovery] end (60s)
- `   146s` [rolling_restart] begin
- `   150s` [rolling_restart] restarted n2
- `   154s` [rolling_restart] restarted n3
- `   158s` [rolling_restart] restarted n4
- `   162s` [rolling_restart] restarted n5
- `   172s` [rolling_restart] end (26s)
- `   172s` [partition] begin
- `   174s` [partition] n1 and n2 isolated at height 37
- `   222s` [partition] partition healed: n1 and n2 rejoined with P2P
- `   222s` [partition] final total work 317408976
- `   222s` [partition] end (50s)
- `   222s` [late_joiner] begin
- `   232s` [late_joiner] wiped n5's data directory (network height 56); starting it from nothing
- `   233s` [late_joiner] end (11s)
- `   233s` [tx_flood] begin
- `   383s` [tx_flood] funding transactions submitted; waiting for them to confirm
- `   475s` [tx_flood] submitted 33 transfers across 5 nodes (129 rejected at submission)
- `   488s` [tx_flood] end (255s)
- `   488s` [attack] begin
- `   489s` [attack] end (1s)
- `   489s` [corruption] begin
- `   492s` [corruption] end (3s)
- `   492s` [difficulty] begin
- `   553s` [difficulty] phase '1 miner': {'label': '1 miner', 'miners': 1, 'blocks': 14, 'seconds': 60, 'interval_mean': 4, 'interval_late_mean': 4.71, 'difficulty_start': 7370632, 'difficulty_end': 9177396}
- `   634s` [difficulty] phase '6 miners (6x hashrate)': {'label': '6 miners (6x hashrate)', 'miners': 6, 'blocks': 16, 'seconds': 80, 'interval_mean': 4.6, 'interval_late_mean': 5.75, 'difficulty_start': 8023769, 'difficulty_end': 8173351}
- `   724s` [difficulty] phase '1 miner again (1/6 hashrate)': {'label': '1 miner again (1/6 hashrate)', 'miners': 1, 'blocks': 18, 'seconds': 90, 'interval_mean': 4.82, 'interval_late_mean': 5.11, 'difficulty_start': 7772388, 'difficulty_end': 8547277}
- `   724s` [difficulty] end (232s)
- `   724s` [final] begin
- `   729s` [final] end (5s)

## Known limitations of this test

- One machine, one clock, loopback networking: no latency, jitter, packet loss, or clock skew was exercised.
- Partitions are emulated by disabling a node's P2P layer, not by blocking traffic between live peers.
- Hashrate comes from single-threaded Python CPU miners; the absolute numbers say nothing about a real network.
- Five nodes, one operator. Nothing here tests independently operated nodes or hostile majority behaviour.
- Disk-full, out-of-memory, and power-loss (as opposed to process-kill) failures were not tested.
