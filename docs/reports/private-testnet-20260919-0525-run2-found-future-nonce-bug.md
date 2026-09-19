> **Run 2 of 3 (kept for the record).** 52/57 checks passed. The attack, corruption, partition and recovery scenarios all passed.
> The transaction flood failed and exposed a **real bug**: a node that received a sender's transaction N+1 before N rejected it as
> `bad_nonce`, marked it seen, never re-requested it and never relayed it. Fixed with a bounded holding pool (`PROTOCOL.md` 10.4).
> The difficulty scenario failed because its phases (24 blocks) were shorter than the 30-block difficulty window and started from the
> previous phase's state; an offline recomputation from the kept database showed all 120 declared difficulties matched the algorithm
> exactly (0 mismatches). The scenario was redesigned with longer phases and directional checks.

# Private testnet report (Milestone 7)

- Generated: 2026-09-19 05:25:10 local time, run time 10.9 minutes
- Network: `privnet` (target block time 5 s, initial difficulty 16,384, LWMA window 30); five separate `mineai node` OS processes on 127.0.0.1
- Machine: Windows-10-10.0.26200-SP0, 24 CPUs, Python 3.10.0
- **Result: 52/57 checks passed, 5 FAILED**

This is a single-machine test: all nodes share one clock and reach each other over loopback with no latency or packet loss. Partitions are emulated by disabling P2P on a node, not by dropping packets. See Known limitations.

## Scenarios

| Scenario | Checks | Time |
|---|---|---|
| setup | 3/3 | 11 s |
| baseline | 2/2 | 76 s |
| kill_recovery | 10/10 | 61 s |
| rolling_restart | 2/2 | 27 s |
| partition | 4/4 | 50 s |
| late_joiner | 2/2 | 11 s |
| tx_flood | 4/6 | 107 s |
| attack | 14/14 | 9 s |
| corruption | 5/5 | 4 s |
| difficulty | 0/3 | 292 s |
| final | 6/6 | 5 s |

## Every check

| Scenario | Check | Result | Detail |
|---|---|---|---|
| setup | all 5 nodes start and answer the health check | PASS | 5/5 ready |
| setup | all nodes share the pinned privnet genesis | PASS | 1 distinct |
| setup | seeded as a chain, discovery forms a mesh (every node has >= 3 peers) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| baseline | chain grows under two competing miners | PASS | height 22 |
| baseline | after mining stops, all 5 nodes agree on the same tip | PASS | height 22 |
| kill_recovery | kill #1: n4's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-pn6uj023\n4\privnet\chain.db verified: height 22, total work 106217408, minte |
| kill_recovery | kill #1: n4 restarts | PASS |  |
| kill_recovery | kill #1: n4 catches up to the live network (within 2 blocks) | PASS | 26 vs 26 |
| kill_recovery | kill #2: n2's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-pn6uj023\n2\privnet\chain.db verified: height 30, total work 253818667, minte |
| kill_recovery | kill #2: n2 restarts | PASS |  |
| kill_recovery | kill #2: n2 catches up to the live network (within 2 blocks) | PASS | 33 vs 33 |
| kill_recovery | kill #3: n5's database is consistent right after the crash | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-pn6uj023\n5\privnet\chain.db verified: height 34, total work 304917050, minte |
| kill_recovery | kill #3: n5 restarts | PASS |  |
| kill_recovery | kill #3: n5 catches up to the live network (within 2 blocks) | PASS | 37 vs 37 |
| kill_recovery | after 3 crashes and restarts all nodes converge on one tip | PASS | height 37 |
| rolling_restart | after a rolling restart of 4 nodes under load, all nodes converge | PASS | height 42 |
| rolling_restart | the mesh re-forms from persisted peers (>= 2 peers each) | PASS | {'n1': 4, 'n2': 4, 'n3': 4, 'n4': 4, 'n5': 4} |
| partition | while partitioned the two sides really diverge | PASS | heights {'n1': 53, 'n2': 42, 'n3': 46, 'n4': 46, 'n5': 46} |
| partition | after healing, all 5 nodes converge on one chain | PASS | height 53 |
| partition | every node on the LIGHTER side reorganized (and the heavier side did not) | PASS | reorgs gained {'n1': 0, 'n2': 0, 'n3': 1, 'n4': 1, 'n5': 1}; lighter side ['n3', 'n4', 'n5'] |
| partition | the surviving chain is the one with the most cumulative work | PASS | final 587,453,303 vs max side 587,453,303 |
| late_joiner | a wiped node performs a full initial sync from peers | PASS | 53+ blocks in 0.6s |
| late_joiner | and ends on exactly the same tip as everyone else | PASS |  |
| tx_flood | the funding account has mined and matured enough to fund the flood (>= 400 MAI) | PASS | 1075.0 MAI available |
| tx_flood | 8 funding transactions confirm | **FAIL** |  |
| tx_flood | every submitted transaction was accepted by the node it was sent to | **FAIL** | 39 ok, 129 rejected |
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
| corruption | after discarding the damaged database the node resyncs and converges | PASS | height 82 |
| difficulty | difficulty rises when hashrate rises 6x | **FAIL** | 22135251 -> 20918621 |
| difficulty | difficulty falls again when hashrate drops | **FAIL** | 20918621 -> 21946161 |
| difficulty | block spacing settles near the 5 s target in every phase (2.5 s - 12 s) | **FAIL** | [3, 20.4] |
| final | network converges at the end | PASS |  |
| final | offline full verification of n1's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-pn6uj023\n1\privnet\chain.db verified: height 119, total work 1741836632, min |
| final | offline full verification of n2's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-pn6uj023\n2\privnet\chain.db verified: height 119, total work 1741836632, min |
| final | offline full verification of n3's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-pn6uj023\n3\privnet\chain.db verified: height 119, total work 1741836632, min |
| final | offline full verification of n4's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-pn6uj023\n4\privnet\chain.db verified: height 119, total work 1741836632, min |
| final | offline full verification of n5's database (`mineai check`) | PASS | OK: privnet database at C:\Users\USER\AppData\Local\Temp\mineai-privnet-pn6uj023\n5\privnet\chain.db verified: height 119, total work 1741836632, min |

## Failures

- **tx_flood: 8 funding transactions confirm** — 
- **tx_flood: every submitted transaction was accepted by the node it was sent to** — 39 ok, 129 rejected
- **difficulty: difficulty rises when hashrate rises 6x** — 22135251 -> 20918621
- **difficulty: difficulty falls again when hashrate drops** — 20918621 -> 21946161
- **difficulty: block spacing settles near the 5 s target in every phase (2.5 s - 12 s)** — [3, 20.4]

## Measurements

- Baseline block interval with 2 miners: mean 1.62 s, median 1.0 s (target 5 s); reorganizations seen by then: 0
- Initial sync of a wiped node: 53 blocks in 0.6 s
- Transaction flood: 39 transactions accepted across 5 nodes, all mined

Difficulty response to a real hashrate change:

| Phase | Miners | Blocks | Mean interval | Mean interval, 2nd half | Difficulty at start | at end |
|---|---|---|---|---|---|---|
| 1 miner | 1 | 3 | 25 s | None s | 26,521,848 | 22,135,251 |
| 6 miners (6x hashrate) | 6 | 24 | 2.87 s | 3 s | 20,488,095 | 20,918,621 |
| 1 miner again (1/6 hashrate) | 1 | 10 | 14 s | 20.4 s | 22,319,991 | 21,946,161 |

## Final counters per node

| Node | Height | Blocks accepted | Blocks rejected | Reorgs | Blocks connected in reorgs | Peers banned | Penalty points | Tx accepted | Tx rejected |
|---|---|---|---|---|---|---|---|---|---|
| n1 | 119 | 66 | 0 | 0 | 0 | 0 | 0 | 39 | 24 |
| n2 | 119 | 77 | 2 | 0 | 0 | 6 | 650 | 39 | 18 |
| n3 | 119 | 119 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| n4 | 119 | 80 | 1 | 1 | 5 | 0 | 0 | 39 | 23 |
| n5 | 119 | 119 | 1 | 0 | 0 | 0 | 0 | 39 | 34 |

Counters restart from zero whenever a node is killed, so they cover only the last run of each process.

## Monitoring summary (1 Hz poll of every node)

- 607 snapshots; all live nodes on the same tip in 92% of them
- longest continuous disagreement: about 42 s (includes deliberate partitions and restarts)
- highest chain height seen: 119

## Timeline

- `     0s` [setup] begin
- `    11s` [setup] end (11s)
- `    11s` [baseline] begin
- `    11s` [baseline] 2 miners started on n1 and n3
- `    87s` [baseline] block interval mean 1.6s median 1.0s over 16 blocks
- `    87s` [baseline] reorganizations observed across nodes so far: 0
- `    87s` [baseline] end (76s)
- `    87s` [kill_recovery] begin
- `    93s` [kill_recovery] HARD-KILLED n4 at network height 22 (no graceful shutdown)
- `   113s` [kill_recovery] HARD-KILLED n2 at network height 30 (no graceful shutdown)
- `   132s` [kill_recovery] HARD-KILLED n5 at network height 34 (no graceful shutdown)
- `   148s` [kill_recovery] end (61s)
- `   148s` [rolling_restart] begin
- `   151s` [rolling_restart] restarted n2
- `   155s` [rolling_restart] restarted n3
- `   159s` [rolling_restart] restarted n4
- `   163s` [rolling_restart] restarted n5
- `   174s` [rolling_restart] end (27s)
- `   174s` [partition] begin
- `   176s` [partition] n1 and n2 isolated at height 42
- `   221s` [partition] before healing: n1 work 587,453,303 vs majority work 478,983,567 -> the isolated node is heavier
- `   224s` [partition] partition healed: n1 and n2 rejoined with P2P
- `   224s` [partition] final total work 587453303
- `   224s` [partition] end (50s)
- `   224s` [late_joiner] begin
- `   234s` [late_joiner] wiped n5's data directory (network height 53); starting it from nothing
- `   235s` [late_joiner] end (11s)
- `   235s` [tx_flood] begin
- `   235s` [tx_flood] funding transactions submitted; waiting for them to confirm
- `   326s` [tx_flood] submitted 35 transfers across 5 nodes (129 rejected at submission)
- `   343s` [tx_flood] end (107s)
- `   343s` [attack] begin
- `   351s` [attack] end (9s)
- `   351s` [corruption] begin
- `   355s` [corruption] end (4s)
- `   355s` [difficulty] begin
- `   416s` [difficulty] phase '1 miner': {'label': '1 miner', 'miners': 1, 'blocks': 3, 'seconds': 60, 'interval_mean': 25, 'interval_late_mean': None, 'difficulty_start': 26521848, 'difficulty_end': 22135251}
- `   497s` [difficulty] phase '6 miners (6x hashrate)': {'label': '6 miners (6x hashrate)', 'miners': 6, 'blocks': 24, 'seconds': 80, 'interval_mean': 2.87, 'interval_late_mean': 3, 'difficulty_start': 20488095, 'difficulty_end': 20918621}
- `   648s` [difficulty] phase '1 miner again (1/6 hashrate)': {'label': '1 miner again (1/6 hashrate)', 'miners': 1, 'blocks': 10, 'seconds': 150, 'interval_mean': 14, 'interval_late_mean': 20.4, 'difficulty_start': 22319991, 'difficulty_end': 21946161}
- `   648s` [difficulty] end (292s)
- `   648s` [final] begin
- `   652s` [final] end (5s)

## Known limitations of this test

- One machine, one clock, loopback networking: no latency, jitter, packet loss, or clock skew was exercised.
- Partitions are emulated by disabling a node's P2P layer, not by blocking traffic between live peers.
- Hashrate comes from single-threaded Python CPU miners; the absolute numbers say nothing about a real network.
- Five nodes, one operator. Nothing here tests independently operated nodes or hostile majority behaviour.
- Disk-full, out-of-memory, and power-loss (as opposed to process-kill) failures were not tested.
