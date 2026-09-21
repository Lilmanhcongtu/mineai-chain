# Installation guide (0.2.0rc1, testnet release candidate)

MineAI here is **test software for a test network with no monetary value**. Never reuse a real password for a wallet, and
expect the network to be reset without notice.

## 0. Verify what you downloaded

Compare the SHA-256 of every file with `SHA256SUMS.txt`, and get that file from a channel you trust (the artifacts are unsigned):

```powershell
Get-FileHash .\MineAI-0.2.0rc1-windows.zip -Algorithm SHA256        # Windows
sha256sum -c SHA256SUMS.txt                                         # Linux / macOS
```

## 1. Windows (packaged, no Python needed)

```powershell
Expand-Archive MineAI-0.2.0rc1-windows.zip -DestinationPath .
cd MineAI-0.2.0rc1-windows
.\install.ps1                       # per-user install: no administrator rights, no services, nothing starts by itself
# or run straight from this folder without installing
```

Windows SmartScreen or antivirus may warn about an unsigned executable. Only continue if the checksum matches.

Run on the **testnet** (the default network is `devnet`; select `testnet` explicitly):

```powershell
$env:MINEAI_NETWORK = "testnet"
.\mineai-node.cmd                                                    # API + explorer on http://127.0.0.1:18080, P2P on 127.0.0.1:18081
.\mineai-wallet.cmd --network testnet create --wallet me.testnet.wallet.json
.\mineai-wallet.cmd --network testnet address --wallet me.testnet.wallet.json
.\mineai-miner.cmd --network testnet --address <TMAI...> --blocks 5   # mines only while this command runs; Ctrl+C stops it
.\mineai-wallet.cmd --network testnet shell --wallet me.testnet.wallet.json
```

## 2. Linux (from source)

Requires Python 3.10 or newer (`python3 --version`) with the `venv` module (on Debian/Ubuntu: `sudo apt install python3-venv`).
CI runs this whole section on Ubuntu for every commit; other distributions and macOS have not been tried.

```bash
tar xzf mineai-0.2.0rc1-source.tar.gz && cd mineai-0.2.0rc1      # or: git clone https://github.com/Lilmanhcongtu/mineai-chain.git && cd mineai-chain
sh packaging/linux/install.sh
export PATH="$HOME/.local/bin:$PATH"                              # if ~/.local/bin is not on your PATH yet
```

The script creates a private virtualenv in `~/.local/share/mineai` (override with `PREFIX=`) and four commands in
`~/.local/bin` (override with `BIN=`): `mineai`, `mineai-node`, `mineai-wallet`, `mineai-miner`. It never touches wallets
or chain data. Then, on the **testnet**:

```bash
export MINEAI_NETWORK=testnet
mineai version
mineai-node                                                       # API + explorer on http://127.0.0.1:18080, P2P on 127.0.0.1:18081 (Ctrl+C stops it)
```

In a second terminal (the wallet asks for a password of at least 10 characters):

```bash
export MINEAI_NETWORK=testnet
mineai wallet --network testnet create  --wallet me.testnet.wallet.json
mineai wallet --network testnet address --wallet me.testnet.wallet.json
mineai miner  --network testnet --address <TMAI...> --blocks 5      # mines only while this command runs; Ctrl+C stops it
mineai wallet --network testnet balance --wallet me.testnet.wallet.json
```

To run a node as a service, see `DEPLOY_VPS.md`. To remove it: `rm -rf ~/.local/share/mineai ~/.local/bin/mineai*`
(your data directory is not touched).

## 3. From source (any OS)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q                              # optional: run the test suite
.\.venv\Scripts\python.exe -m mineai version
```

## Joining the testnet

**There are no public seed nodes yet.** To connect nodes to each other, give each one a seed:

```powershell
$env:MINEAI_NETWORK = "testnet"
$env:MINEAI_SEEDS = "203.0.113.10:18081,203.0.113.11:18081"          # host:port of nodes you or others operate
.\mineai-node.cmd
```

By default the node listens on `127.0.0.1` only. To accept connections from other machines set `MINEAI_P2P_HOST=0.0.0.0`
(P2P) and open TCP 18081 in your firewall. **P2P is unencrypted and unauthenticated**: do that only on networks you trust or
accept the risk (see `SECURITY_CHECKLIST.md`). Leave the HTTP API (`MINEAI_HOST`) on `127.0.0.1`.

## Where things live

| What | Where |
|---|---|
| Chain data | `%USERPROFILE%\.mineai\testnet\chain.db` (`~/.mineai/testnet/chain.db`); override with `MINEAI_DATA_DIR` |
| Wallets | the file name you give; **back them up** (`wallet backup --verify-password`); no recovery without file **and** password |
| Logs | JSON lines on stderr; redirect to a file if you want to keep them |

## Verify your node

```powershell
Invoke-RestMethod http://127.0.0.1:18080/api/v1/status      # network "testnet", genesis 2ddd2b53...d335, height, peers, sync status
Invoke-RestMethod http://127.0.0.1:18080/metrics            # Prometheus text
mineai check --network testnet                              # offline full verification (stop the node first)
```

## Uninstall

`.\uninstall.ps1` removes the program only. Your wallets and chain data are never deleted by it.
