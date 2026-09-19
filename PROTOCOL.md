# MineAI Protocol Specification — v1 (draft, Milestone 3)

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
| `network_id` (ASCII) | `mineai-devnet-v2` | `mineai-testnet-v1` | `mineai-mainnet-v1` |
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
       || u64(timestamp) || u32_be(difficulty) || u64(nonce)
hash   = hex( SHA-256( header ) )
```

### 5.5 Proof of work and difficulty

`difficulty` is the number of leading **hexadecimal zeros** the block hash must have. It is fixed by the
network profile (devnet: 4) — **dynamic difficulty is Milestone 5**. A block whose `difficulty` differs from the network value is invalid.
Chain work (used from Milestone 4) is defined as `16^difficulty` per block.

### 5.6 Genesis

Height 0, `previous_hash = merkle_root = 0×64`, `difficulty = 0`, `nonce = 0`, no transactions,
`timestamp = genesis_timestamp` (2026-01-01T00:00:00Z for devnet/testnet). No PoW check, no premine, no coinbase.
The devnet genesis hash is pinned in `config.py` and by a test:
`c35e979832d87b29f354fc92ddf932d58f50bee71e3048c9b28079f21d7b612f`.

### 5.7 Timestamps

For a block at height `h ≥ 1`:
* `timestamp > median(timestamps of the last 11 blocks)` (fewer near genesis). It may be older than the previous block.
* `timestamp ≤ local_clock + 7200 s`. This rule depends on the validator's clock and is the only non-deterministic check; a block rejected for being too far in the future may become valid later.

### 5.8 Size limits

* Transaction ≤ 1024 bytes; at most 500 regular transactions per block; the sum of all transaction sizes ≤ 512 KiB.
* The API additionally limits request bodies (default 64 KiB) — that is policy, not consensus.

### 5.9 Block validity (summary)

Structure and types strict → sizes → every transaction valid (§4) and unique → merkle root → difficulty →
hash and PoW → extends the current tip (`height = tip+1`, `previous_hash = tip.hash`) → timestamp rules →
state rules for each transaction in order → coinbase rule → commit atomically.

## 6. Coinbase maturity

A coinbase amount credited in block `k` cannot be spent until height `k + maturity`. Precisely, at
height `h`, `immature(addr, h)` = sum of coinbase amounts to `addr` in blocks with
`h − maturity + 1 ≤ height ≤ h − 1`.

## 7. Monetary policy

* Block subsidy 25 MAI (25 000 000 atomic). Maximum supply 100 000 000 MAI. **Premine: 0.**
* `subsidy = min(reward, max_supply − minted_supply)`, never negative. The last subsidy is clipped; afterwards blocks pay only fees.
* Fees are transfers from senders to the miner and **never increase supply**. Invariant, checked on every open: `sum(balances) == minted_supply`.
* Minimum fee 0.001 MAI; default wallet fee 0.01 MAI. There is no halving schedule in this version; see `TOKENOMICS.md`.

## 8. Chain selection and reorganizations — **[NOT YET SPECIFIED]**

Milestone 4 will specify cumulative-work chain selection. Until then a node has one linear chain and
rejects any block that does not extend its tip (`stale`). The storage schema already records per-block
undo data (`state_diffs`) in preparation.

## 9. Mempool policy (not consensus)

A node accepts a transaction only if it is valid (§4), not a duplicate (confirmed or pending), its
`timestamp` is within ±24 h of the local clock, `nonce` is exactly the sender's next nonce
(confirmed nonce + pending count + 1), and `amount + fee ≤ balance − immature − pending outgoing`.
Limits: 5 000 transactions total, 25 per sender. No replace-by-fee. After each block the mempool is
re-validated and unusable transactions are dropped. Templates order by fee, keeping per-sender nonce order.

## 10. Peer-to-peer protocol (Milestone 3, protocol version 1)

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
`height`, `tip_hash`, `listen_port` (0..65535, 0 = not listening), `node_id` (32 hex characters, random per process),
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
| `get_blocks` | `{from_height:1..2^31, count:1..64}` | request consecutive blocks from a height |
| `blocks` | `{blocks:[≤64 blocks]}` | reply to `get_blocks`; **only accepted if we asked** |

### 10.4 Relay and synchronization

* A newly accepted transaction or block is announced with `inv` to every peer not already known to have it (never back to its source). An `inv` for an item we already have, or have already requested from someone (for up to the request timeout), causes no request. A `tx`/`block` already seen is dropped without reprocessing or rebroadcast.
* When a peer's `hello` height, or a received block, shows it is ahead, we request `get_blocks` starting at our tip + 1, apply blocks in order through normal block validation, and repeat until a batch is short or makes no progress. Only one synchronization per peer runs at a time.
* **Linear chain only:** a block that does not extend our tip is ignored (no penalty if it is merely old or competing). Competing forks are **not** resolved in this version; nodes that mine different blocks at the same height stay apart. Cumulative-work chain selection and reorganization are Milestone 4.

### 10.5 Peer management and abuse handling (policy, not consensus)

* Limits: 16 peers total, 12 inbound, 4 outbound targets; 5 s connect timeout; 20 s write timeout; 20 s request timeout.
* Rate limit per peer: 50 messages/second with a burst of 100; excess messages are dropped and scored.
* Misbehavior score per peer identity (accumulated across reconnects): malformed frame/JSON 50; unknown message type 20; invalid transaction 20 (1 for races such as duplicate/nonce/insufficient funds); invalid block 50 (5 for a timestamp problem); invalid block during synchronization 100; unsolicited `blocks` 20 (non-fatal); rate-limit excess 10 each; protocol-level violations that are fatal close the connection.
* A score of 100 bans the identity for 10 minutes (node id, and IP for non-loopback addresses; loopback is never IP-banned so one bad local process cannot block every local node).
* Known peer addresses are persisted in the database, exchanged with `get_peers`, retried with failure counting (dropped after 10 failures, configured seeds never), and used to reconnect after restarts.
* P2P binds to 127.0.0.1 by default. There are no administrative P2P messages.

## 11. Stored data

SQLite, schema version tracked with `PRAGMA user_version` and forward-only migrations. Schema v2 adds the `peers` table. A database
records its `network_id` and genesis hash and is refused by any other network. Legacy V0.1 databases are refused, never modified.
