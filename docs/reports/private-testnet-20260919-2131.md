# Private testnet report (Milestone 7)

- Generated: 2026-09-19 21:31:52 local time, run time 12.2 minutes
- Network: `privnet` (target block time 5 s, initial difficulty 16,384, LWMA window 30); five separate `mineai node` OS processes on 127.0.0.1
- Machine: Windows-10-10.0.26200-SP0, 24 CPUs, Python 3.10.0
- **Result: 59/60 checks passed, 1 FAILED**

This is a single-machine test: all nodes share one clock and reach each other over loopback with no latency or packet loss. Partitions are emulated by disabling P2P on a node, not by dropping packets. See Known limitations.

## Scenarios

| Scenario | Checks | Time |
|---|---|---|
| setup | 3/3 | 11 s |
| baseline | 2/2 | 75 s |
| kill_recovery | 10/10 | 61 s |
| rolling_restart | 2/2 | 27 s |
| partition | 4/4 | 49 s |
| late_joiner | 2/2 | 11 s |
| tx_flood | 6/6 | 27 s |
| attack | 14/14 | 23 s |
| corruption | 5/5 | 3 s |
| difficulty | 5/6 | 443 s |
| final | 6/6 | 3 s |

## Every check

| Scenario | Check | Result | Detail |
|---|---|---|---|
| setup | all 5 nodes start and answer the health check | PASS | 5/5 ready |
| setup | all nodes share the pinned privnet genesis | PASS | 1 distinct |
| setup | seeded as a chain, discovery forms a mesh (every node has >= 3 peers) | PASS | {'n1': 4, 'n2': 3, 'n3': 4, 'n4': 4, 'n5': 3} |
| baseline | chain grows under two competing miners | PASS | height 21 |
| baseline | after mining stops, all 5 nodes agree on the same tip | PASS | height 21 |
| kill_recovery | kill #1: n4's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jp_840p3\n4\privnet\chain.db verified: height 23, total work 116434767, minte |
| kill_recovery | kill #1: n4 restarts | PASS |  |
| kill_recovery | kill #1: n4 catches up to the live network (within 2 blocks) | PASS | 28 vs 28 |
| kill_recovery | kill #2: n2's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jp_840p3\n2\privnet\chain.db verified: height 30, total work 165004060, minte |
| kill_recovery | kill #2: n2 restarts | PASS |  |
| kill_recovery | kill #2: n2 catches up to the live network (within 2 blocks) | PASS | 40 vs 40 |
| kill_recovery | kill #3: n5's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jp_840p3\n5\privnet\chain.db verified: height 44, total work 314309896, minte |
| kill_recovery | kill #3: n5 restarts | PASS |  |
| kill_recovery | kill #3: n5 catches up to the live network (within 2 blocks) | PASS | 46 vs 46 |
| kill_recovery | after 3 crashes and restarts all nodes converge on one tip | PASS | height 46 |
| rolling_restart | after a rolling restart of 4 nodes under load, all nodes converge | PASS | height 49 |
| rolling_restart | the mesh re-forms from persisted peers (>= 2 peers each) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| partition | while partitioned the two sides really diverge | PASS | heights {'n1': 59, 'n2': 49, 'n3': 51, 'n4': 51, 'n5': 51} |
| partition | after healing, all 5 nodes converge on one chain | PASS | height 59 |
| partition | every node on the LIGHTER side reorganized (and the heavier side did not) | PASS | reorgs gained {'n1': 0, 'n2': 0, 'n3': 1, 'n4': 1, 'n5': 1}; lighter side ['n3', 'n4', 'n5'] |
| partition | the surviving chain is the one with the most cumulative work | PASS | final 575,591,773 vs max side 575,591,773 |
| late_joiner | a wiped node performs a full initial sync from peers | PASS | 59+ blocks in 0.6s |
| late_joiner | and ends on exactly the same tip as everyone else | PASS |  |
| tx_flood | the funding account has mined and matured enough to fund the flood (>= 400 MAI) | PASS | 525.0 MAI available |
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
| corruption | after discarding the damaged database the node resyncs and converges | PASS | height 63 |
| difficulty | phase A: with a matching hashrate the block spacing is near the 5 s target (2.5 s - 12 s) | PASS | mean 3.63 s |
| difficulty | phase B: blocks speed up right after hashrate rises (first third faster than 70% of target) | PASS | 2.75 s |
| difficulty | phase B: difficulty then climbs (last-third median >= 1.3x phase A's) | PASS | 14,761,628 -> 37,981,173 |
| difficulty | phase B: block spacing recovers toward the target by the end (last third within 2 s - 12 s) | PASS | 3.5 s |
| difficulty | phase C: blocks slow down right after hashrate falls (first third slower than 1.5x target) | **FAIL** | 5.25 s |
| difficulty | phase C: difficulty then falls (last-third median <= 0.9x phase B's) | PASS | 37,981,173 -> 26,539,595 |
| final | network converges at the end | PASS |  |
| final | offline full verification of n1's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jp_840p3\n1\privnet\chain.db verified: height 143, total work 2479660048, min |
| final | offline full verification of n2's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jp_840p3\n2\privnet\chain.db verified: height 143, total work 2479660048, min |
| final | offline full verification of n3's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jp_840p3\n3\privnet\chain.db verified: height 143, total work 2479660048, min |
| final | offline full verification of n4's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jp_840p3\n4\privnet\chain.db verified: height 143, total work 2479660048, min |
| final | offline full verification of n5's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-jp_840p3\n5\privnet\chain.db verified: height 143, total work 2479660048, min |

## Failures

- **difficulty: phase C: blocks slow down right after hashrate falls (first third slower than 1.5x target)** — 5.25 s

## Measurements

- Baseline block interval with 2 miners: mean 2.6 s, median 1 s (target 5 s); reorganizations seen by then: 4
- Initial sync of a wiped node: 59 blocks in 0.6 s
- Transaction flood: 168 transactions accepted across 5 nodes, all mined

Difficulty response to real hashrate changes (measured from the chain's block timestamps):

| Phase | Miners | Blocks | Mean interval | First third | Last third | Difficulty start | end | Last-third median |
|---|---|---|---|---|---|---|---|---|
| A: 3 miners | 3 | 28 | 3.63 s | 3.11 s | 4.22 s | 13,008,239 | 14,399,888 | 14,761,628 |
| B: 10 miners (3.3x hashrate) | 10 | 39 | 2.82 s | 2.75 s | 3.5 s | 15,339,401 | 40,149,560 | 37,981,173 |
| C: 3 miners again (0.3x hashrate) | 3 | 13 | 15.5 s | 5.25 s | 15 s | 43,109,887 | 22,403,286 | 26,539,595 |

## Final counters per node

| Node | Height | Blocks accepted | Blocks rejected | Reorgs | Blocks connected in reorgs | Peers banned | Penalty points | Tx accepted | Tx rejected |
|---|---|---|---|---|---|---|---|---|---|
| n1 | 143 | 84 | 0 | 0 | 0 | 0 | 0 | 168 | 0 |
| n2 | 143 | 94 | 2 | 0 | 0 | 6 | 650 | 168 | 0 |
| n3 | 143 | 143 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| n4 | 143 | 96 | 1 | 1 | 3 | 0 | 0 | 168 | 0 |
| n5 | 143 | 143 | 0 | 0 | 0 | 0 | 0 | 168 | 0 |

Counters restart from zero whenever a node is killed, so they cover only the last run of each process.

## Monitoring summary (1 Hz poll of every node)

- 705 snapshots; all live nodes on the same tip in 94% of them
- longest continuous disagreement: about 45 s (includes deliberate partitions and restarts)
- highest chain height seen: 143

## Timeline

- `     0s` [setup] begin
- `    11s` [setup] end (11s)
- `    11s` [baseline] begin
- `    11s` [baseline] 2 miners started on n1 and n3
- `    86s` [baseline] block interval mean 2.6s median 1.0s over 15 blocks
- `    86s` [baseline] reorganizations observed across nodes so far: 4
- `    86s` [baseline] end (75s)
- `    86s` [kill_recovery] begin
- `    92s` [kill_recovery] HARD-KILLED n4 at network height 23 (no graceful shutdown)
- `   112s` [kill_recovery] HARD-KILLED n2 at network height 30 (no graceful shutdown)
- `   131s` [kill_recovery] HARD-KILLED n5 at network height 43 (no graceful shutdown)
- `   147s` [kill_recovery] end (61s)
- `   147s` [rolling_restart] begin
- `   151s` [rolling_restart] restarted n2
- `   156s` [rolling_restart] restarted n3
- `   160s` [rolling_restart] restarted n4
- `   164s` [rolling_restart] restarted n5
- `   175s` [rolling_restart] end (27s)
- `   175s` [partition] begin
- `   177s` [partition] n1 and n2 isolated at height 49
- `   222s` [partition] before healing: n1 work 575,591,773 vs majority work 450,634,204 -> the isolated node is heavier
- `   224s` [partition] partition healed: n1 and n2 rejoined with P2P
- `   224s` [partition] final total work 575591773
- `   224s` [partition] end (49s)
- `   224s` [late_joiner] begin
- `   234s` [late_joiner] wiped n5's data directory (network height 59); starting it from nothing
- `   235s` [late_joiner] end (11s)
- `   235s` [tx_flood] begin
- `   235s` [tx_flood] funding transactions submitted; waiting for them to confirm
- `   244s` [tx_flood] submitted 160 transfers across 5 nodes (0 rejected at submission)
- `   261s` [tx_flood] end (27s)
- `   261s` [attack] begin
- `   284s` [attack] end (23s)
- `   284s` [corruption] begin
- `   287s` [corruption] end (3s)
- `   287s` [difficulty] begin
- `   407s` [difficulty] phase 'A: 3 miners': {'label': 'A: 3 miners', 'miners': 3, 'blocks': 28, 'seconds': 120, 'interval_mean': 3.63, 'interval_first_third': 3.11, 'interval_last_third': 4.22, 'difficulty_start': 13008239, 'difficulty_end': 14399888, 'difficulty_last_third_median': 14761628}
- `   529s` [difficulty] phase 'B: 10 miners (3.3x hashrate)': {'label': 'B: 10 miners (3.3x hashrate)', 'miners': 10, 'blocks': 39, 'seconds': 120, 'interval_mean': 2.82, 'interval_first_third': 2.75, 'interval_last_third': 3.5, 'difficulty_start': 15339401, 'difficulty_end': 40149560, 'difficulty_last_third_median': 37981173}
- `   729s` [difficulty] phase 'C: 3 miners again (0.3x hashrate)': {'label': 'C: 3 miners again (0.3x hashrate)', 'miners': 3, 'blocks': 13, 'seconds': 200, 'interval_mean': 15.5, 'interval_first_third': 5.25, 'interval_last_third': 15, 'difficulty_start': 43109887, 'difficulty_end': 22403286, 'difficulty_last_third_median': 26539595}
- `   729s` [difficulty] end (443s)
- `   729s` [final] begin
- `   733s` [final] end (3s)

## Known limitations of this test

- One machine, one clock, loopback networking: no latency, jitter, packet loss, or clock skew was exercised.
- Partitions are emulated by disabling a node's P2P layer, not by blocking traffic between live peers.
- Hashrate comes from single-threaded Python CPU miners; the absolute numbers say nothing about a real network.
- Five nodes, one operator. Nothing here tests independently operated nodes or hostile majority behaviour.
- Disk-full, out-of-memory, and power-loss (as opposed to process-kill) failures were not tested.
