# Sandbox network (pre-allocated genesis)

`sandbox` is a **local experiment network** that starts with 20,000,000 MAI already allocated at block 0, so you can
test wallets, transfers and load without mining for months. It is **not** a MineAI tokenomics change:

* Every public profile (`devnet`, `testnet`, `mainnet`) and `privnet` keep a **zero** premine. A test
  (`tests/unit/test_sandbox.py`) fails if any of them gains a genesis allocation.
* The sandbox has its own network id (`mineai-sandbox-v1`), `SMAI` addresses and ports 48080/48081, so its coins and
  transactions cannot be replayed on, or moved to, any other network.
* Coins have **no monetary value**. Never present a sandbox balance as a real holding.

## How it works

`NetworkParams.genesis_allocations` lists `(address, atomic amount)` pairs. When set:

* the allocation is committed into the genesis block (`merkle_root` = hash of the allocations), so nodes only share a
  genesis if they agree on every balance; a different amount is a different chain;
* the balances are credited when the database is created, and count as **minted supply**, so the 100,000,000 MAI cap
  still applies to allocation + block rewards together;
* `mineai check` replays the allocation before block 1, and opening a database re-checks `sum(balances) == minted supply`.

The allocation is defined in `mineai/config.py` (`SANDBOX`) for the wallet address it was created with.

## Use

```powershell
$env:MINEAI_NETWORK = "sandbox"
.\.venv\Scripts\python.exe -m mineai node                     # API/explorer on http://127.0.0.1:48080
.\.venv\Scripts\python.exe -m mineai wallet balance --wallet sandbox.wallet.json --node http://127.0.0.1:48080
```

To allocate to a different address: create a wallet with `MINEAI_NETWORK=sandbox`, put its address in `SANDBOX.genesis_allocations`,
recompute the genesis hash (`C.genesis_block(SANDBOX)["hash"]`), update `genesis_hash`, and start with a fresh data directory.
