# MineAI Protocol Specification — v1 (draft, Milestone 5)

> **Status:** covers the rules **implemented and tested today** (consensus rules plus the Milestone 3 peer-to-peer protocol). Sections marked
> **[NOT YET SPECIFIED]** are planned for later milestones and are deliberately empty: nothing in the
> code may implement behaviour that this document does not describe. When code and this document
> disagree, that is a bug in one of them. The golden vectors in `tests/unit/test_encoding.py`
> pin the byte-level encodings below.
>
> MineAI is prototype software for a development network. It has **no monetary value**.

## 1. Networks

| | devnet | testnet | mainnet |
|---|---|---|---|
| `network_id` (ASCII) | `mineai-devnet-v3` | `mineai-testnet-v1` | `mineai-mainnet-v1` |
| address prefix | `DMAI` | `TMAI` | `MAI` |
| default API port | 8080 | 18080 | 28080 |
| default P2P port | 8081 | 18081 | 28081 |
| coinbase maturity | 3 blocks | 10 | 100 |
| status | usable locally | genesis not yet created | **disabled**, not launched |

The `network_id` is bound into every transaction signature, txid, coinbase id and block hash, and
the address prefix is bound into every address checksum. A transaction or block from one network is
therefore invalid on every other network. (V0.1 had neither, so its signed transactions could be
replayed across chains.)

Consensus parameters are constants of the network profile (`mineai/config.py`); they are not read
from environment variables.

## 2. Units and integers

* 1 MAI = 1 000 000 atomic units (6 decimals). All consensus arithmetic is on integers of atomic units. Floating point is never used.
* Every consensus integer is an unsigned integer in `[0, 2^63 − 1]` and is encoded on the wire as 8-byte big-endian (`u64`). Values outside the range, and JSON values that are not integers (`true`, `5.0`, `"5"`, `null`), are invalid.
* Encoding helpers: `u64(n)` = 8-byte big-endian; `lp(s)` = 1-byte length followed by the ASCII bytes of `s` (max 255).

## 3. Addresses

```
hash160   = SHA-256(public_key)[0:20]
checksum  = SHA-256(PREFIX_ascii || hash160)[0:4]
address   = PREFIX || Base32(hash160 || checksum)      # RFC 4648, upper-case, no padding, 39 characters
```

An address is valid only in this exact canonical form: right prefix, 39 characters from `A–Z2–7`,
zero trailing bits (re-encoding the decoded bytes must reproduce the string), correct checksum.
Lower-case and other aliases are invalid, so one owner cannot appear as two account keys.

Public keys are Ed25519 (32 bytes). The address is a hash of the key, so the key travels with each transaction.

## 4. Transactions

A regular transaction is a JSON object with **exactly** these fields (no more, no fewer):

| field | type | rule |
|---|---|---|
| `sender` | string | valid address of this network |
| `recipient` | string | valid address of this network, ≠ `sender` |
| `amount` | integer | `1 ≤ amount ≤ max_supply` |
| `fee` | integer | `min_fee ≤ fee ≤ max_supply` |
| `nonce` | integer | ≥ 1; must equal the sender's account nonce + 1 at inclusion |
| `timestamp` | integer | `0 ≤ t ≤ 2^63−1`; informational (see §4.3) |
| `public_key` | string | base64url (padded), canonical, decodes to 32 bytes |
| `signature` | string | base64url (padded), canonical, decodes to 64 bytes |
| `txid` | string | 64 lower-case hex characters |

"Canonical base64" means re-encoding the decoded bytes reproduces the string exactly.

### 4.1 Signing payload

```
payload = "MineAI/tx/v1\0" || lp(network_id) || lp(sender) || lp(recipient)
        || u64(amount) || u64(fee) || u64(nonce) || u64(timestamp) || public_key(32 bytes)
signature = Ed25519_sign(private_key, payload)
```

### 4.2 Transaction ID

```
txid = hex( SHA-256( payload || signature(64 bytes) ) )
```

Validity requires: address = `address(public_key)` == `sender`; the signature verifies over `payload`;
`txid` equals the recomputed value; the serialized transaction (compact JSON, sorted keys, ASCII)
is at most `max_tx_bytes` (1024).

### 4.3 State rules (applied in block order)

For a transaction in a block at height `h`:
1. `nonce == sender.nonce + 1`.
2. `amount + fee ≤ sender.balance − immature(sender, h)` (§6).
3. Then `sender.balance −= amount + fee`, `sender.nonce = nonce`, `recipient.balance += amount`. The fee is collected by the miner through the coinbase (§5).
4. A `txid` already present in the chain is invalid (also implied by nonce rules).

The `timestamp` field is signed but has **no consensus meaning**; it exists for mempool policy (§9).

## 5. Blocks

### 5.1 Structure

A block is a JSON object with exactly: `height`, `previous_hash`, `merkle_root`, `timestamp`,
`difficulty`, `nonce`, `hash`, `transactions`. `transactions[0]` is the coinbase; the rest are
regular transactions. Explorer APIs additionally show derived fields (`miner`, `subsidy`, `fees`), which are not part of the block.

### 5.2 Coinbase

```
{ "type":"coinbase", "txid":…, "height":h, "recipient":addr, "subsidy":s, "fees":f, "amount":s+f }
coinbase_txid = hex( SHA-256( "MineAI/coinbase/v1\0" || lp(network_id) || u64(h) || lp(recipient) || u64(s) || u64(f) ) )
```

`subsidy` must equal the subsidy rule (§7); `fees` must equal the sum of the block's transaction fees;
`amount = subsidy + fees`. Including the height makes every coinbase id unique. There is exactly one coinbase and it is first.

### 5.3 Merkle root

Leaves are the transaction ids (coinbase first) as 32-byte values. `leaf = SHA-256(0x00 || txid)`,
`node = SHA-256(0x01 || left || right)`. An unpaired node is promoted to the next level unchanged (never
duplicated). The root of an empty list is 32 zero bytes. Duplicate transaction ids in a block are invalid.

### 5.4 Header and block hash

```
header = "MineAI/block/v1\0" || lp(network_id) || u64(height) || previous_hash(32) || merkle_root(32)
       || u64(timestamp) || u64(difficulty) || u64(nonce)
hash   = hex( SHA-256( header ) )
```

### 5.5 Proof of work

`difficulty` (D) is a positive integer, `1 ≤ D ≤ 2^62`: the expected number of hash attempts needed to find a block.

```
target(D) = floor( (2^256 − 1) / D )
valid PoW  <=>  int_be(block hash) ≤ target(D)          # the 32-byte hash read as a big-endian integer
```

A block is invalid unless its declared `difficulty` equals the value required by section 5.10 for its parent.
Difficulty is therefore a function of the block's own ancestry (never of the local tip), so competing branches are judged correctly.
(V0.1–V0.2 used "number of leading hexadecimal zeros"; that representation moved in 16x steps, too coarse for retargeting. `difficulty`
changed meaning together with the header layout, hence the new network id `mineai-devnet-v3`.)

### 5.6 Genesis

Height 0, `previous_hash = merkle_root = 0×64`, `difficulty = 0` (no proof of work), `nonce = 0`, no transactions,
`timestamp = genesis_timestamp` (2026-01-01T00:00:00Z for devnet/testnet). No PoW check, no premine, no coinbase.
The devnet genesis hash is pinned in `config.py` and by a test:
`b163bf62f484bcaf90db3f821f8e70becb702444d2f912df7680e62d3d885666`.

### 5.7 Timestamps

For a block at height `h ≥ 1`:
* `timestamp > median(timestamps of the last 11 blocks)` (fewer near genesis). It may be older than the previous block.
* `timestamp ≤ local_clock + 300 s`. This rule depends on the validator's clock and is the only non-deterministic check; a block rejected for being too far in the future may become valid later.

### 5.8 Size limits

* Transaction ≤ 1024 bytes; at most 500 regular transactions per block; the sum of all transaction sizes ≤ 512 KiB.
* The API additionally limits request bodies (default 64 KiB) — that is policy, not consensus.

### 5.9 Block validity (summary)

Structure and types strict → sizes → every transaction valid (§4) and unique → merkle root → difficulty →
hash and PoW → extends the current tip (`height = tip+1`, `previous_hash = tip.hash`) → timestamp rules →
state rules for each transaction in order → coinbase rule → commit atomically.

### 5.10 Difficulty adjustment (Milestone 5)

Goal: one block every `T = 60` seconds on average, following hashrate changes within a few dozen blocks,
without oscillating and without letting timestamp games buy an advantage. The algorithm is a linearly-weighted
moving average (LWMA) of recent solve times over **median-filtered timestamps**, with a solve-time cap and a per-block
change limit. It uses **integers only** and depends only on the parent's ancestry.

Parameters (network profile): `T = 60`, window `N = 60`, timestamp filter `F = 5`, initial difficulty `D0`,
`min_difficulty`, maximum `2^62`. Profiles with `dynamic_difficulty = false` (test profiles only) always use `D0`.

Let `P` be the parent. Let `hist` be `P` and up to `N + F − 1` of its ancestors, oldest first, entries `hist[0..c−1]`
(the list starts at the genesis block when the chain is that short), each with its timestamp `t` and declared difficulty `D`.

1. Let `w = min(N, c − F)`. If `w < 1` (heights 0 to 4: fewer than `F` blocks of history) the required difficulty is `D0`.
2. Filtered timestamps: `f(i) = ` the median of `t[i−F+1 … i]` (the upper median when the count is even; always `F` values here).
3. `prev = f(c − w − 1)`. For `j = 1 … w` with `i = c − w − 1 + j`:
   * `eff = f(i)` if `f(i) > prev`, otherwise `prev + 1` (strictly increasing timestamps);
   * `st = min(6·T, eff − prev)` (a solve time is capped at six targets);
   * `prev = eff`; `L += j · st`; `S += D[i]` (the difficulty declared by that block).
4. `D_raw = floor( S · (w + 1) · T / (2 · L) )`. With constant hashrate this equals the average difficulty (exactly, in integers); if blocks are slower than `T` it falls, if faster it rises.
5. Limit the per-block change with `D_P` = the parent's difficulty: `D = min(D_raw, 2·D_P)` and `D = max(D, floor(D_P / 2))`.
6. Clamp: `D = max(D, min_difficulty)`, `D = min(D, 2^62)`, and never below 1.

Why these choices (each is exercised by `tests/consensus/test_difficulty.py`, using seeded simulations of a memoryless
proof-of-work search; the figures below are from those simulations and are not guarantees for a real network):

* *Steady state:* at constant hashrate the mean block time is within ~2% of 60 s.
* *Hashrate x10:* blocks come roughly every 11 s for about 60 blocks, the difficulty converges to ~10x, then 60 s is restored. *Hashrate /10:* blocks are slow (~4.5 min) while it adapts (about 30 blocks), then 60 s is restored. There is no emergency adjustment, so a large collapse costs real time.
* *Long network pause:* a solve time is capped at `6T`, and the median filter hides a single late block until later blocks confirm it, so a one-day pause lowers the difficulty by only ~10-15% and the following hour holds ~60 blocks. (An absolute-schedule algorithm such as ASERT was rejected: after a one-day pause it mined ~400 blocks in the first hour.)
* *Oscillation:* a hashrate alternating between 3x and 1/3x every 30 blocks moves the difficulty by about 3.6x, less than the 9x input swing, and the variance does not grow over time.
* *Timestamp manipulation:* a block may be stamped at most `max_future_seconds = 300` ahead of the validator's clock and must exceed the median of the last 11 blocks. The median filter removes isolated outliers, so only sustained manipulation matters. Attackers (30% of hashrate) stamping forward, backdating to the minimum, or alternating keep the real block time within ~5% of 60 s; a constant offset has no lasting effect (only differences count). Even at 45% of the hashrate, the worst strategy measured (backdating) slows blocks by about 1.5x. In no strategy tested does the average difficulty fall below the honest level, so manipulation cannot make blocks cheaper. Its only effect is to slow the chain down, at the manipulator's own cost.
* *Cost of the tighter clock limit:* a node whose clock is more than 5 minutes fast will find its blocks rejected until the clock is fixed.
* *Startup:* the first 5 blocks use `D0`; a `D0` far from the real hashrate is corrected at the maximum rate of 2x per block.

## 6. Coinbase maturity

A coinbase amount credited in block `k` cannot be spent until height `k + maturity`. Precisely, at
height `h`, `immature(addr, h)` = sum of coinbase amounts to `addr` in blocks with
`h − maturity + 1 ≤ height ≤ h − 1`.

## 7. Monetary policy

* Block subsidy 25 MAI (25 000 000 atomic). Maximum supply 100 000 000 MAI. **Premine: 0.**
* `subsidy = min(reward, max_supply − minted_supply)`, never negative. The last subsidy is clipped; afterwards blocks pay only fees.
* Fees are transfers from senders to the miner and **never increase supply**. Invariant, checked on every open: `sum(balances) == minted_supply`.
* Minimum fee 0.001 MAI; default wallet fee 0.01 MAI. There is no halving schedule in this version; see `TOKENOMICS.md`.

## 8. Chain selection and reorganizations (Milestone 4)

### 8.1 Chain work

`work(block) = difficulty` for blocks at height ≥ 1 and `work(genesis) = 0`.
`total_work(block) = total_work(parent) + work(block)`. Work values are unbounded integers
(stored as decimal strings; compared as integers).

### 8.2 Best chain

A node considers every block it has received whose ancestry it knows. The **best chain** is the valid chain
with the greatest `total_work` — *not* the greatest height, so a shorter chain with more work wins.
**Ties:** the chain whose tip was received first stays best (no switching on equal work).
The difficulty a block must declare is `expected_difficulty(parent)` (section 5.10), evaluated against the block's own parent chain, never the local tip.

### 8.3 Block classes on receipt

After stateless validation (§5.9 structure, sizes, merkle root, proof of work, declared difficulty):

| class | condition | action |
|---|---|---|
| duplicate | hash already stored | ignore |
| invalid | the parent (or the block itself) is known-invalid | reject, never retried |
| orphan | parent unknown | keep in a bounded in-memory pool (100 blocks); request the missing ancestry; connect when the parent arrives |
| extends the tip | parent is the current best tip | validate against state (§5.9) and connect |
| side | parent known but not the tip | store; state is **not** evaluated yet |

A stored side block whose `total_work` exceeds the tip's triggers a reorganization to it.
Stateful rules (§4.3, §5.7 timestamps against *that branch's* recent blocks, §5.2 coinbase, §6 maturity) are checked when a block is connected, in the context of its own branch.

### 8.4 Reorganization

To switch from tip `T` to a better tip `N`, with fork point `F` (the newest common ancestor):
1. Disconnect blocks from `T` back to `F`, restoring each block's recorded pre-state (balances, nonces, minted supply, transaction index).
2. Connect the blocks from `F` to `N` in order, validating each fully.
3. Do 1 and 2 **in one atomic database transaction**. If any block fails validation, everything is rolled back (the old chain is untouched), that block and its descendants are marked invalid permanently, and the reorganization is rejected. A block whose only problem is a timestamp too far in the future is *not* marked invalid (it may become valid later).
4. Mempool: transactions from disconnected blocks that are not in the new branch are re-admitted in their original order when still valid (nonce, funds; the ±24 h timestamp window is not applied); transactions confirmed by the new branch are removed; everything unusable is then pruned. Coinbase outputs of disconnected blocks disappear with their blocks.
5. Disconnected blocks remain stored as side blocks, so the abandoned branch can win again if it later gains more work.

### 8.5 Depth limit (policy, not consensus)

A node refuses to reorganize more than `max_reorg_depth` blocks (devnet/testnet: 100) and refuses to store new side blocks whose fork point is deeper than that.
This bounds the cost of deep-fork attacks but **can split the network permanently** if a partition outlasts the limit; an operator would have to resync from scratch. Side blocks deeper than the limit, and beyond 2000 stored side blocks, are pruned.

### 8.6 Locator-based synchronization

`get_blocks` carries a *locator*: up to 32 hashes of the requester's best chain, newest first — the last 10 blocks one by one, then with exponentially growing gaps, always ending with genesis. The responder finds the first locator hash that is on *its* best chain and returns up to `count` blocks after it (from height 1 if none match). This finds the fork point across divergent chains in one round trip.

## 9. Mempool policy (not consensus)

A node accepts a transaction only if it is valid (§4), not a duplicate (confirmed or pending), its
`timestamp` is within ±24 h of the local clock, `nonce` is exactly the sender's next nonce
(confirmed nonce + pending count + 1), and `amount + fee ≤ balance − immature − pending outgoing`.
Limits: 5 000 transactions total, 25 per sender. No replace-by-fee. After each block the mempool is
re-validated and unusable transactions are dropped. Templates order by fee, keeping per-sender nonce order.

## 10. Peer-to-peer protocol (protocol version 1, extended in Milestone 4)

Nodes talk over TCP. Consensus is never decided by the network layer: every block and transaction
received is validated by the rules above before it is stored or relayed.

### 10.1 Framing

```
frame = u32_be(length) || body          # 1 <= length <= limit; the length is checked BEFORE the body is read
body  = UTF-8 JSON  {"v": <int>, "type": <string>, "data": <object>}     # exactly these three keys
```

* Limits: 8 KiB until the handshake completes, then 4 MiB per message. A larger declared length closes the connection immediately.
* JSON is parsed strictly: `NaN`/`Infinity` are rejected; invalid UTF-8, non-objects, extra or missing keys, unknown `type`, a `v` that is not an integer (booleans are not integers) or not the negotiated version, and any `data` that does not match the schema of its type are protocol violations.
* Integers must be JSON integers within the stated range; strings must match the stated pattern.
* Transactions and blocks inside messages are the exact objects of sections 4 and 5 and are validated by the consensus rules.

### 10.2 Handshake and versions

Each side sends `hello` immediately after connecting (envelope `v` = 1) and must receive the peer's `hello`
first (within 10 s). `hello.data` has exactly: `min_version`, `max_version` (1..1000), `network_id`, `genesis_hash`,
`height`, `tip_hash`, `total_work` (decimal string, ≤ 100 digits), `listen_port` (0..65535, 0 = not listening), `node_id` (32 hex characters, random per process),
`user_agent` (≤64 characters of `[A-Za-z0-9._/- ]`).

The connection is refused if: `network_id` or `genesis_hash` differ; there is no common protocol version
(the negotiated version is the highest version supported by both, and every later envelope carries it);
`node_id` equals our own (self-connection) or is already connected (duplicate); the peer is banned; the peer table is full.
This software speaks version 1 only. A second `hello` after the handshake is a violation.

### 10.3 Messages

| type | data | meaning |
|---|---|---|
| `ping` / `pong` | `{nonce}` | liveness (every 30 s); a peer silent for 90 s is dropped |
| `get_peers` / `peers` | `{}` / `{addrs:[≤50 "host:port"]}` | peer discovery |
| `inv` | `{kind:"tx"\|"block", hashes:[1..500 hex]}` | announce that we have items |
| `get_data` | same shape | request announced items (at most 16 are served per request) |
| `tx` / `block` | `{tx}` / `{block}` | the item itself (may also be pushed unsolicited) |
| `get_blocks` | `{locator:[1..32 hashes], count:1..64}` | request blocks after the newest locator hash on the responder's best chain (§8.6) |
| `blocks` | `{blocks:[≤64 blocks]}` | reply to `get_blocks`; **only accepted if we asked** |

### 10.4 Relay and synchronization

* A newly accepted transaction or block is announced with `inv` to every peer not already known to have it (never back to its source). An `inv` for an item we already have, or have already requested from someone (for up to the request timeout), causes no request. A `tx`/`block` already seen is dropped without reprocessing or rebroadcast.
* When a peer's `hello` shows more total work than ours, or a received block is an orphan, we synchronize: send `get_blocks` with our locator, process each returned block with the block classes of §8.3 (reorganizations happen automatically as work accumulates), and repeat until a batch is short or nothing new was learned. Only one synchronization per peer runs at a time.
* Whenever our best tip changes (extension or reorganization) we announce the new tip with `inv` to peers that do not know it. Side blocks are not announced.
* A block whose fork would exceed the depth limit (§8.5) is dropped without penalty; peers on a chain we refuse to follow are simply not synchronized with.

### 10.5 Peer management and abuse handling (policy, not consensus)

* Limits: 16 peers total, 12 inbound, 4 outbound targets; 5 s connect timeout; 20 s write timeout; 20 s request timeout.
* Rate limit per peer: 50 messages/second with a burst of 100; excess messages are dropped and scored.
* Misbehavior score per peer identity (accumulated across reconnects): malformed frame/JSON 50; unknown message type 20; invalid transaction 20 (1 for races such as duplicate/nonce/insufficient funds); invalid block 50 (5 for a timestamp problem; 0 for a fork that is merely too deep); invalid block during synchronization 100; unsolicited `blocks` 20 (non-fatal); rate-limit excess 10 each; protocol-level violations that are fatal close the connection.
* A score of 100 bans the identity for 10 minutes (node id, and IP for non-loopback addresses; loopback is never IP-banned so one bad local process cannot block every local node).
* Known peer addresses are persisted in the database, exchanged with `get_peers`, retried with failure counting (dropped after 10 failures, configured seeds never), and used to reconnect after restarts.
* P2P binds to 127.0.0.1 by default. There are no administrative P2P messages.

## 11. Stored data

SQLite, schema version tracked with `PRAGMA user_version` and forward-only migrations (v3 adds `total_work` to blocks and the `side_blocks` table). Schema v2 adds the `peers` table. A database
records its `network_id` and genesis hash and is refused by any other network. Legacy V0.1 databases are refused, never modified.
