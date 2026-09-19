# Contributing

## Ground rules

1. **The specification leads.** Consensus behaviour must be documented in `PROTOCOL.md` *before or with* the code. Do not add undocumented consensus rules. Any change to an encoding or rule needs a spec update, updated golden vectors, and (once networks exist) a protocol-version decision.
2. **Small, reviewable changes.** One concern per change; include tests.
3. **Never commit secrets:** private keys, wallet files, passwords, `.env`, databases, logs. Check `git status` before committing.
4. **Tests must test behaviour.** For every new rule add a test that submits a violating input and asserts the specific rejection code *and* that state is unchanged. Do not weaken or delete a failing test to make it pass.
5. **No scope creep:** no smart contracts, token sales, exchanges, custody, pools, governance or mainnet launch work.

## Setup

```powershell
.\scripts\setup_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest --cov=mineai --cov-report=term-missing
```

Python 3.10+ is supported. The multi-process private testnet program (`scripts\private_testnet.py run`, ~15 minutes) is separate from the unit tests; run it before releases and after changes to networking, storage or consensus. To build the Windows package: `.\scripts\build_windows.ps1` (runs the tests first; `-SkipTests` to skip).

## Layout

```
mineai/consensus.py   pure consensus rules (no I/O, no framework)
mineai/blockchain.py  state machine over storage
mineai/storage.py     SQLite + migrations
mineai/crypto.py      addresses, encodings, signatures, wallet encryption
mineai/node.py        FastAPI API + explorer (create_app factory)
mineai/miner.py, wallet.py   CLIs
tests/{unit,consensus,integration,adversarial}
```

Consensus code must not import FastAPI or use wall-clock time directly (time is passed in). Database
changes need a new forward-only migration in `storage.MIGRATIONS`; never edit an existing migration.

## Commit style

Imperative subject line, body explains *why*. Reference the `PROTOCOL.md` section for consensus changes.
