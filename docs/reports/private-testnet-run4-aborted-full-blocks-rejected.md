# Private testnet run 4 (aborted): a full mempool could never be mined

Run 4 of 5 was stopped in the transaction-flood scenario. It produced no report. Setup, baseline, crash recovery, rolling
restart, partition and late-joiner scenarios passed; the burst-relay fix from run 3 worked (all five nodes received all 160
transactions, nobody was banned, every mempool was identical).

## What happened
After the flood, every node held exactly 160 pending transactions and the chain stopped at height 68: nothing was being mined and
no miner process was still running.

## Cause
A block template with 160 transactions is 69,366 bytes of JSON. The API rejects request bodies over 64 KiB
(`MINEAI_MAX_BODY_BYTES`, default 65536), and miners submit blocks through the API. Consensus allows blocks up to 512 KiB
and 500 transactions, so **any block with more than about 150 transactions could never be submitted**. The miner received
`413 too_large`, treated it as a hard rejection and exited, and the backlog could never drain.

## Fix
The body limit is now per endpoint: `mining/submit` accepts up to twice the consensus maximum block size (JSON overhead);
every other endpoint keeps the 64 KiB cap. Regression tests build a real 161-transaction block (> 64 KiB) and submit it
through the API; they fail (HTTP 413) on the previous code and pass now (`tests/integration/test_api.py`).
