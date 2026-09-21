# Private testnet report (Milestone 7)

- Generated: 2026-09-20 21:40:28 local time, run time 12.0 minutes
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
| tx_flood | 6/6 | 24 s |
| attack | 14/14 | 10 s |
| corruption | 5/5 | 3 s |
| difficulty | 6/6 | 444 s |
| final | 6/6 | 4 s |

## Every check

| Scenario | Check | Result | Detail |
|---|---|---|---|
| setup | all 5 nodes start and answer the health check | PASS | 5/5 ready |
| setup | all nodes share the pinned privnet genesis | PASS | 1 distinct |
| setup | seeded as a chain, discovery forms a mesh (every node has >= 3 peers) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| baseline | chain grows under two competing miners | PASS | height 28 |
| baseline | after mining stops, all 5 nodes agree on the same tip | PASS | height 28 |
| kill_recovery | kill #1: n4's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-a20hui61\n4\privnet\chain.db verified: height 28, total work 93500177, minted |
| kill_recovery | kill #1: n4 restarts | PASS |  |
| kill_recovery | kill #1: n4 catches up to the live network (within 2 blocks) | PASS | 36 vs 36 |
| kill_recovery | kill #2: n2's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-a20hui61\n2\privnet\chain.db verified: height 39, total work 166726839, minte |
| kill_recovery | kill #2: n2 restarts | PASS |  |
| kill_recovery | kill #2: n2 catches up to the live network (within 2 blocks) | PASS | 42 vs 42 |
| kill_recovery | kill #3: n5's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-a20hui61\n5\privnet\chain.db verified: height 44, total work 221568857, minte |
| kill_recovery | kill #3: n5 restarts | PASS |  |
| kill_recovery | kill #3: n5 catches up to the live network (within 2 blocks) | PASS | 46 vs 46 |
| kill_recovery | after 3 crashes and restarts all nodes converge on one tip | PASS | height 46 |
| rolling_restart | after a rolling restart of 4 nodes under load, all nodes converge | PASS | height 53 |
| rolling_restart | the mesh re-forms from persisted peers (>= 2 peers each) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| partition | while partitioned the two sides really diverge | PASS | heights {'n1': 64, 'n2': 53, 'n3': 58, 'n4': 58, 'n5': 58} |
| partition | after healing, all 5 nodes converge on one chain | PASS | height 64 |
| partition | every node on the LIGHTER side reorganized (and the heavier side did not) | PASS | reorgs gained {'n1': 0, 'n2': 0, 'n3': 1, 'n4': 1, 'n5': 1}; lighter side ['n3', 'n4', 'n5'] |
| partition | the surviving chain is the one with the most cumulative work | PASS | final 483,981,723 vs max side 483,981,723 |
| late_joiner | a wiped node performs a full initial sync from peers | PASS | 65+ blocks in 0.6s |
| late_joiner | and ends on exactly the same tip as everyone else | PASS |  |
| tx_flood | the funding account has mined and matured enough to fund the flood (>= 400 MAI) | PASS | 600.0 MAI available |
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
| corruption | after discarding the damaged database the node resyncs and converges | PASS | height 69 |
| difficulty | phase A: with a matching hashrate the block spacing is near the 5 s target (2.5 s - 12 s) | PASS | mean 5.09 s |
| difficulty | phase B: blocks speed up right after hashrate rises (first third faster than 70% of target) | PASS | 1.27 s |
| difficulty | phase B: difficulty then climbs (last-third median >= 1.3x phase A's) | PASS | 11,168,756 -> 49,961,805 |
| difficulty | phase B: block spacing recovers toward the target by the end (last third within 2 s - 12 s) | PASS | 8.09 s |
| difficulty | phase C: blocks slow down right after hashrate falls (first third slower than 1.5x target) | PASS | 9.91 s |
| difficulty | phase C: difficulty then falls (last-third median <= 0.9x phase B's) | PASS | 49,961,805 -> 25,321,400 |
| final | network converges at the end | PASS |  |
| final | offline full verification of n1's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-a20hui61\n1\privnet\chain.db verified: height 162, total work 2746909576, min |
| final | offline full verification of n2's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-a20hui61\n2\privnet\chain.db verified: height 162, total work 2746909576, min |
| final | offline full verification of n3's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-a20hui61\n3\privnet\chain.db verified: height 162, total work 2746909576, min |
| final | offline full verification of n4's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-a20hui61\n4\privnet\chain.db verified: height 162, total work 2746909576, min |
| final | offline full verification of n5's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-a20hui61\n5\privnet\chain.db verified: height 162, total work 2746909576, min |

## Measurements

- Baseline block interval with 2 miners: mean 3.32 s, median 1.5 s (target 5 s); reorganizations seen by then: 3
- Initial sync of a wiped node: 65 blocks in 0.6 s
- Transaction flood: 168 transactions accepted across 5 nodes, all mined

Difficulty response to real hashrate changes (measured from the chain's block timestamps):

| Phase | Miners | Blocks | Mean interval | First third | Last third | Difficulty start | end | Last-third median |
|---|---|---|---|---|---|---|---|---|
| A: 3 miners | 3 | 24 | 5.09 s | 2 s | 6 s | 13,475,124 | 11,062,645 | 11,168,756 |
| B: 10 miners (3.3x hashrate) | 10 | 34 | 3.61 s | 1.27 s | 8.09 s | 10,145,410 | 34,358,055 | 49,961,805 |
| C: 3 miners again (0.3x hashrate) | 3 | 35 | 5.68 s | 9.91 s | 3.91 s | 35,856,569 | 27,779,638 | 25,321,400 |

## Final counters per node

| Node | Height | Blocks accepted | Blocks rejected | Reorgs | Blocks connected in reorgs | Peers banned | Penalty points | Tx accepted | Tx rejected |
|---|---|---|---|---|---|---|---|---|---|
| n1 | 162 | 98 | 1 | 0 | 0 | 0 | 0 | 168 | 14 |
| n2 | 162 | 109 | 2 | 0 | 0 | 6 | 650 | 168 | 0 |
| n3 | 162 | 162 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| n4 | 162 | 113 | 0 | 1 | 5 | 0 | 0 | 168 | 0 |
| n5 | 162 | 162 | 0 | 0 | 0 | 0 | 0 | 168 | 0 |

Counters restart from zero whenever a node is killed, so they cover only the last run of each process.

## Monitoring summary (1 Hz poll of every node)

- 681 snapshots; all live nodes on the same tip in 93% of them
- longest continuous disagreement: about 44 s (includes deliberate partitions and restarts)
- highest chain height seen: 162

## Timeline

- `     0s` [setup] begin
- `    11s` [setup] end (11s)
- `    11s` [baseline] begin
- `    11s` [baseline] 2 miners started on n1 and n3
- `    87s` [baseline] block interval mean 3.3s median 1.5s over 22 blocks
- `    87s` [baseline] reorganizations observed across nodes so far: 3
- `    87s` [baseline] end (76s)
- `    87s` [kill_recovery] begin
- `    93s` [kill_recovery] HARD-KILLED n4 at network height 28 (no graceful shutdown)
- `   113s` [kill_recovery] HARD-KILLED n2 at network height 39 (no graceful shutdown)
- `   132s` [kill_recovery] HARD-KILLED n5 at network height 44 (no graceful shutdown)
- `   147s` [kill_recovery] end (60s)
- `   147s` [rolling_restart] begin
- `   151s` [rolling_restart] restarted n2
- `   154s` [rolling_restart] restarted n3
- `   158s` [rolling_restart] restarted n4
- `   162s` [rolling_restart] restarted n5
- `   172s` [rolling_restart] end (26s)
- `   172s` [partition] begin
- `   174s` [partition] n1 and n2 isolated at height 53
- `   219s` [partition] before healing: n1 work 483,981,723 vs majority work 398,596,020 -> the isolated node is heavier
- `   221s` [partition] partition healed: n1 and n2 rejoined with P2P
- `   221s` [partition] final total work 483981723
- `   221s` [partition] end (49s)
- `   221s` [late_joiner] begin
- `   231s` [late_joiner] wiped n5's data directory (network height 65); starting it from nothing
- `   232s` [late_joiner] end (11s)
- `   232s` [tx_flood] begin
- `   232s` [tx_flood] funding transactions submitted; waiting for them to confirm
- `   238s` [tx_flood] submitted 160 transfers across 5 nodes (0 rejected at submission)
- `   256s` [tx_flood] end (24s)
- `   256s` [attack] begin
- `   266s` [attack] end (10s)
- `   266s` [corruption] begin
- `   269s` [corruption] end (3s)
- `   269s` [difficulty] begin
- `   390s` [difficulty] phase 'A: 3 miners': {'label': 'A: 3 miners', 'miners': 3, 'blocks': 24, 'seconds': 120, 'interval_mean': 5.09, 'interval_first_third': 2, 'interval_last_third': 6, 'difficulty_start': 13475124, 'difficulty_end': 11062645, 'difficulty_last_third_median': 11168756}
- `   513s` [difficulty] phase 'B: 10 miners (3.3x hashrate)': {'label': 'B: 10 miners (3.3x hashrate)', 'miners': 10, 'blocks': 34, 'seconds': 120, 'interval_mean': 3.61, 'interval_first_third': 1.27, 'interval_last_third': 8.09, 'difficulty_start': 10145410, 'difficulty_end': 34358055, 'difficulty_last_third_median': 49961805}
- `   714s` [difficulty] phase 'C: 3 miners again (0.3x hashrate)': {'label': 'C: 3 miners again (0.3x hashrate)', 'miners': 3, 'blocks': 35, 'seconds': 200, 'interval_mean': 5.68, 'interval_first_third': 9.91, 'interval_last_third': 3.91, 'difficulty_start': 35856569, 'difficulty_end': 27779638, 'difficulty_last_third_median': 25321400}
- `   714s` [difficulty] end (444s)
- `   714s` [final] begin
- `   718s` [final] end (4s)

## Known limitations of this test

- One machine, one clock, loopback networking: no latency, jitter, packet loss, or clock skew was exercised.
- Partitions are emulated by disabling a node's P2P layer, not by blocking traffic between live peers.
- Hashrate comes from single-threaded Python CPU miners; the absolute numbers say nothing about a real network.
- Five nodes, one operator. Nothing here tests independently operated nodes or hostile majority behaviour.
- Disk-full, out-of-memory, and power-loss (as opposed to process-kill) failures were not tested.
