# MineAI Tokenomics (development networks)

> MAI on devnet/testnet has **no monetary value** and is not an investment, security or product for sale.
> Nothing here is a promise of price, profit, listing, or future launch.

| Parameter | Value |
|---|---|
| Name / ticker | MineAI / MAI |
| Decimals | 6 (1 MAI = 1 000 000 atomic units) |
| Maximum supply | 100 000 000 MAI (fixed, enforced by consensus) |
| Premine / founder allocation | **0** (the local-only `sandbox` experiment profile pre-allocates test coins at genesis; see `docs/SANDBOX.md`) |
| Block subsidy | 25 MAI |
| Target block time | ~60 s, enforced by a per-block difficulty adjustment (PROTOCOL.md 5.10) |
| Minimum fee | 0.001 MAI |
| Default wallet fee | 0.01 MAI |
| Coinbase maturity | devnet 3, testnet 10, mainnet 100 (planned) blocks |

* **Issuance:** only through block subsidies. Fees are transfers to the miner and never create coins.
* **Cap enforcement:** `subsidy = min(25 MAI, max_supply − minted)`. Consensus rejects any block that pays more. The invariant `sum(balances) == minted supply` is verified when a database is opened.
* **Open design point (not decided here):** at 25 MAI per 60-second block the cap is reached after roughly 4 million blocks (≈ 7.6 years) with no halving, after which miners depend on fees alone. A halving or decaying schedule must be decided **before** any public testnet genesis is frozen. This is recorded as a question for the project owner, not an implemented rule.
* **No transfer of balances** from V0.1, devnet or testnet to any later network. Each network starts from its own genesis.
* **Out of scope:** presales, token sales, exchange listings, custody, staking or yield.
