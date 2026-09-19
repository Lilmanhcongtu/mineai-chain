MineAI for Windows  --  DEVELOPMENT SOFTWARE, DEVNET/TESTNET ONLY
=================================================================
This is prototype software. It has not been audited. The coins on its networks (MAI) have NO monetary value.
Do not use it for real money, and never reuse a real password for a MineAI wallet.

Verify the download
-------------------
Compare the SHA-256 of the zip with the value published next to it (SHA256SUMS.txt lists every file):
    Get-FileHash .\MineAI-*-windows.zip -Algorithm SHA256
This package is NOT code-signed, so Windows SmartScreen may warn that the publisher is unknown, and some
antivirus products may flag unsigned executables. Only run a copy whose checksum matches the published one.

Install (no administrator rights, no services, nothing starts by itself)
------------------------------------------------------------------------
    .\install.ps1                 copies the program to %LOCALAPPDATA%\Programs\MineAI (asks before touching PATH)
    .\install.ps1 -Target D:\MineAI -NoPath       choose a folder, do not modify PATH
    .\uninstall.ps1               removes the program (your wallets and chain data are NOT deleted)
You can also run everything straight from this folder without installing.

Quick start
-----------
    mineai-node.cmd                                          # devnet node, explorer at http://127.0.0.1:8080
    mineai-wallet.cmd create --wallet me.wallet.json         # you are asked for a password (hidden)
    mineai-wallet.cmd address --wallet me.wallet.json
    mineai-miner.cmd --address <YOUR_ADDRESS> --blocks 4     # mines 4 blocks, then stops; Ctrl+C stops earlier
    mineai-wallet.cmd shell --wallet me.wallet.json          # interactive: balance, history, send, lock, backup
    mineai-wallet.cmd backup --wallet me.wallet.json --to D:\safe\me.backup.json --verify-password

Mining and networking only happen while you run those commands. The node listens on 127.0.0.1 (this machine only)
by default. There is no telemetry, no auto-update and no background mining.

Where things are stored
-----------------------
Chain data:  %USERPROFILE%\.mineai\<network>\chain.db     (set MINEAI_DATA_DIR to change)
Wallets:     wherever you create them (the file name you give). BACK THEM UP: there is no recovery without the file
             AND the password.

Networks
--------
devnet is the default. testnet does not exist yet. mainnet is disabled in this software.
See NETWORK.md, SECURITY.md and PROTOCOL.md in the docs folder.
