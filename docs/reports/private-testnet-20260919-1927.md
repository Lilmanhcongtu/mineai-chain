# Private testnet report (Milestone 7)

- Generated: 2026-09-19 19:27:38 local time, run time 12.1 minutes
- Network: `privnet` (target block time 5 s, initial difficulty 16,384, LWMA window 30); five separate `mineai node` OS processes on 127.0.0.1
- Machine: Windows-10-10.0.26200-SP0, 24 CPUs, Python 3.10.0
- **Result: 60/60 checks passed**

This is a single-machine test: all nodes share one clock and reach each other over loopback with no latency or packet loss. Partitions are emulated by disabling P2P on a node, not by dropping packets. See Known limitations.

## Scenarios

| Scenario | Checks | Time |
|---|---|---|
| setup | 3/3 | 11 s |
| baseline | 2/2 | 76 s |
| kill_recovery | 10/10 | 60 s |
| rolling_restart | 2/2 | 26 s |
| partition | 4/4 | 49 s |
| late_joiner | 2/2 | 11 s |
| tx_flood | 6/6 | 23 s |
| attack | 14/14 | 17 s |
| corruption | 5/5 | 3 s |
| difficulty | 6/6 | 444 s |
| final | 6/6 | 5 s |

## Every check

| Scenario | Check | Result | Detail |
|---|---|---|---|
| setup | all 5 nodes start and answer the health check | PASS | 5/5 ready |
| setup | all nodes share the pinned privnet genesis | PASS | 1 distinct |
| setup | seeded as a chain, discovery forms a mesh (every node has >= 3 peers) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| baseline | chain grows under two competing miners | PASS | height 25 |
| baseline | after mining stops, all 5 nodes agree on the same tip | PASS | height 25 |
| kill_recovery | kill #1: n4's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-d_by12wq\n4\privnet\chain.db verified: height 28, total work 192420010, minte |
| kill_recovery | kill #1: n4 restarts | PASS |  |
| kill_recovery | kill #1: n4 catches up to the live network (within 2 blocks) | PASS | 33 vs 33 |
| kill_recovery | kill #2: n2's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-d_by12wq\n2\privnet\chain.db verified: height 34, total work 248806659, minte |
| kill_recovery | kill #2: n2 restarts | PASS |  |
| kill_recovery | kill #2: n2 catches up to the live network (within 2 blocks) | PASS | 39 vs 39 |
| kill_recovery | kill #3: n5's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-d_by12wq\n5\privnet\chain.db verified: height 39, total work 306532286, minte |
| kill_recovery | kill #3: n5 restarts | PASS |  |
| kill_recovery | kill #3: n5 catches up to the live network (within 2 blocks) | PASS | 42 vs 42 |
| kill_recovery | after 3 crashes and restarts all nodes converge on one tip | PASS | height 42 |
| rolling_restart | after a rolling restart of 4 nodes under load, all nodes converge | PASS | height 45 |
| rolling_restart | the mesh re-forms from persisted peers (>= 2 peers each) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| partition | while partitioned the two sides really diverge | PASS | heights {'n1': 53, 'n2': 45, 'n3': 51, 'n4': 51, 'n5': 51} |
| partition | after healing, all 5 nodes converge on one chain | PASS | height 53 |
| partition | every node on the LIGHTER side reorganized (and the heavier side did not) | PASS | reorgs gained {'n1': 0, 'n2': 0, 'n3': 1, 'n4': 1, 'n5': 1}; lighter side ['n3', 'n4', 'n5'] |
| partition | the surviving chain is the one with the most cumulative work | PASS | final 489,427,467 vs max side 489,427,467 |
| late_joiner | a wiped node performs a full initial sync from peers | PASS | 54+ blocks in 0.6s |
| late_joiner | and ends on exactly the same tip as everyone else | PASS |  |
| tx_flood | the funding account has mined and matured enough to fund the flood (>= 400 MAI) | PASS | 400.0 MAI available |
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
| corruption | after discarding the damaged database the node resyncs and converges | PASS | height 60 |
| difficulty | phase A: with a matching hashrate the block spacing is near the 5 s target (2.5 s - 12 s) | PASS | mean 3.5 s |
| difficulty | phase B: blocks speed up right after hashrate rises (first third faster than 70% of target) | PASS | 2.08 s |
| difficulty | phase B: difficulty then climbs (last-third median >= 1.3x phase A's) | PASS | 15,387,747 -> 50,682,610 |
| difficulty | phase B: block spacing recovers toward the target by the end (last third within 2 s - 12 s) | PASS | 4.17 s |
| difficulty | phase C: blocks slow down right after hashrate falls (first third slower than 1.5x target) | PASS | 12.33 s |
| difficulty | phase C: difficulty then falls (last-third median <= 0.9x phase B's) | PASS | 50,682,610 -> 29,804,686 |
| final | network converges at the end | PASS |  |
| final | offline full verification of n1's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-d_by12wq\n1\privnet\chain.db verified: height 150, total work 2973213572, min |
| final | offline full verification of n2's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-d_by12wq\n2\privnet\chain.db verified: height 150, total work 2973213572, min |
| final | offline full verification of n3's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-d_by12wq\n3\privnet\chain.db verified: height 150, total work 2973213572, min |
| final | offline full verification of n4's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-d_by12wq\n4\privnet\chain.db verified: height 150, total work 2973213572, min |
| final | offline full verification of n5's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-d_by12wq\n5\privnet\chain.db verified: height 150, total work 2973213572, min |

## Measurements

- Baseline block interval with 2 miners: mean 2.89 s, median 1 s (target 5 s); reorganizations seen by then: 1
- Initial sync of a wiped node: 54 blocks in 0.6 s
- Transaction flood: 168 transactions accepted across 5 nodes, all mined

Difficulty response to real hashrate changes (measured from the chain's block timestamps):

| Phase | Miners | Blocks | Mean interval | First third | Last third | Difficulty start | end | Last-third median |
|---|---|---|---|---|---|---|---|---|
| A: 3 miners | 3 | 33 | 3.5 s | 3.1 s | 4.5 s | 9,659,825 | 15,668,809 | 15,387,747 |
| B: 10 miners (3.3x hashrate) | 10 | 37 | 3.17 s | 2.08 s | 4.17 s | 15,914,078 | 62,372,480 | 50,682,610 |
| C: 3 miners again (0.3x hashrate) | 3 | 20 | 9.26 s | 12.33 s | 9.33 s | 62,956,504 | 30,283,434 | 29,804,686 |

## Final counters per node

| Node | Height | Blocks accepted | Blocks rejected | Reorgs | Blocks connected in reorgs | Peers banned | Penalty points | Tx accepted | Tx rejected |
|---|---|---|---|---|---|---|---|---|---|
| n1 | 150 | 97 | 1 | 0 | 0 | 0 | 0 | 168 | 0 |
| n2 | 150 | 105 | 2 | 0 | 0 | 6 | 650 | 168 | 9 |
| n3 | 150 | 150 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| n4 | 150 | 107 | 0 | 1 | 7 | 0 | 0 | 168 | 0 |
| n5 | 150 | 150 | 1 | 0 | 0 | 0 | 0 | 168 | 1 |

Counters restart from zero whenever a node is killed, so they cover only the last run of each process.

## Monitoring summary (1 Hz poll of every node)

- 685 snapshots; all live nodes on the same tip in 93% of them
- longest continuous disagreement: about 44 s (includes deliberate partitions and restarts)
- highest chain height seen: 150

## Timeline

- `     0s` [setup] begin
- `    11s` [setup] end (11s)
- `    11s` [baseline] begin
- `    11s` [baseline] 2 miners started on n1 and n3
- `    87s` [baseline] block interval mean 2.9s median 1.0s over 19 blocks
- `    87s` [baseline] reorganizations observed across nodes so far: 1
- `    87s` [baseline] end (76s)
- `    87s` [kill_recovery] begin
- `    93s` [kill_recovery] HARD-KILLED n4 at network height 27 (no graceful shutdown)
- `   113s` [kill_recovery] HARD-KILLED n2 at network height 34 (no graceful shutdown)
- `   131s` [kill_recovery] HARD-KILLED n5 at network height 39 (no graceful shutdown)
- `   147s` [kill_recovery] end (60s)
- `   147s` [rolling_restart] begin
- `   151s` [rolling_restart] restarted n2
- `   154s` [rolling_restart] restarted n3
- `   158s` [rolling_restart] restarted n4
- `   162s` [rolling_restart] restarted n5
- `   173s` [rolling_restart] end (26s)
- `   173s` [partition] begin
- `   175s` [partition] n1 and n2 isolated at height 45
- `   220s` [partition] before healing: n1 work 489,427,467 vs majority work 472,011,212 -> the isolated node is heavier
- `   222s` [partition] partition healed: n1 and n2 rejoined with P2P
- `   222s` [partition] final total work 489427467
- `   222s` [partition] end (49s)
- `   222s` [late_joiner] begin
- `   232s` [late_joiner] wiped n5's data directory (network height 54); starting it from nothing
- `   233s` [late_joiner] end (11s)
- `   233s` [tx_flood] begin
- `   243s` [tx_flood] funding transactions submitted; waiting for them to confirm
- `   249s` [tx_flood] submitted 160 transfers across 5 nodes (0 rejected at submission)
- `   256s` [tx_flood] end (23s)
- `   256s` [attack] begin
- `   273s` [attack] end (17s)
- `   273s` [corruption] begin
- `   277s` [corruption] end (3s)
- `   277s` [difficulty] begin
- `   398s` [difficulty] phase 'A: 3 miners': {'label': 'A: 3 miners', 'miners': 3, 'blocks': 33, 'seconds': 120, 'interval_mean': 3.5, 'interval_first_third': 3.1, 'interval_last_third': 4.5, 'difficulty_start': 9659825, 'difficulty_end': 15668809, 'difficulty_last_third_median': 15387747}
- `   520s` [difficulty] phase 'B: 10 miners (3.3x hashrate)': {'label': 'B: 10 miners (3.3x hashrate)', 'miners': 10, 'blocks': 37, 'seconds': 120, 'interval_mean': 3.17, 'interval_first_third': 2.08, 'interval_last_third': 4.17, 'difficulty_start': 15914078, 'difficulty_end': 62372480, 'difficulty_last_third_median': 50682610}
- `   721s` [difficulty] phase 'C: 3 miners again (0.3x hashrate)': {'label': 'C: 3 miners again (0.3x hashrate)', 'miners': 3, 'blocks': 20, 'seconds': 200, 'interval_mean': 9.26, 'interval_first_third': 12.33, 'interval_last_third': 9.33, 'difficulty_start': 62956504, 'difficulty_end': 30283434, 'difficulty_last_third_median': 29804686}
- `   721s` [difficulty] end (444s)
- `   721s` [final] begin
- `   726s` [final] end (5s)

## Known limitations of this test

- One machine, one clock, loopback networking: no latency, jitter, packet loss, or clock skew was exercised.
- Partitions are emulated by disabling a node's P2P layer, not by blocking traffic between live peers.
- Hashrate comes from single-threaded Python CPU miners; the absolute numbers say nothing about a real network.
- Five nodes, one operator. Nothing here tests independently operated nodes or hostile majority behaviour.
- Disk-full, out-of-memory, and power-loss (as opposed to process-kill) failures were not tested.
