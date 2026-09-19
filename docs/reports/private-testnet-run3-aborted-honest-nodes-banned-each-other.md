# Private testnet run 3 (aborted): honest nodes banned each other

Run 3 of 4 was stopped during the transaction-flood scenario after it exposed a serious availability bug, so it produced no
report. Setup, baseline, crash recovery, rolling restart, partition and late-joiner scenarios had all passed.

## What happened
While ~170 transactions were submitted through the API, honest nodes relayed them to each other one `inv` message per
transaction. That exceeded the per-peer message rate limit (50 messages/s, burst 100). Excess messages were dropped and each
cost 10 misbehavior points; scores never decayed; at 100 points a peer is banned for 10 minutes. Honest nodes banned honest nodes.

Observed at 05:35 (n = node): n1 and n5 had **0 peers**; n2, n3 and n4 had 2 each; heights 81 / 76 / 76 / 76 / 65. n5's mempool held
101 transactions that were already confirmed on n1 (n5 was simply cut off and behind). The network had split itself under
ordinary traffic.

## Evidence (n5's log, 20 rate-limit penalties recorded before the excerpt)
```
{"ts":"2026-09-19T10:33:57Z","level":"WARNING","logger":"mineai.p2p","event":"peer_banned","peer":"d43a91b150363ab2ded7514b753a0c75","reason":"rate limit exceeded"}
{"ts":"2026-09-19T10:33:57Z","level":"WARNING","logger":"mineai.p2p","event":"peer_banned","peer":"7da7cb5ce80fbd6cbae9747ef74dfdd4","reason":"rate limit exceeded"}
```

## Contributing causes and fixes (all in this milestone)
1. Rate limit too tight for honest bursts -> 200 messages/s, burst 1000, 5 points per violation.
2. Scores never decayed -> one point is forgiven every 5 s (`penalty_decay_seconds`); two quick serious offences still ban at once.
3. Honest races were scored (already-mined, duplicate, unfunded, future-nonce transactions) -> not scored at all.
4. One `inv` message per transaction -> announcements are batched (up to 500 hashes, 50 ms); blocks are still announced immediately.
5. `get_data` served only 16 items per request, which would have silently truncated batched announcements -> 500 transactions
   (blocks stay at 16).

Regression tests: `tests/network/test_burst_resilience.py` (8 tests, including a 4-node mesh relaying 120 transactions with no
bans and no penalty points) and `tests/network/test_future_tx.py`.
