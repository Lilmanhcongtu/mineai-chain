$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot\_common.ps1"
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run .\scripts\setup_windows.ps1 first." }
$py = (Resolve-Path ".venv\Scripts\python.exe").Path
$root = Join-Path $PWD "demo\three-node"
if (Test-Path $root) { Remove-Item -Recurse -Force $root }
New-Item -ItemType Directory -Force -Path $root | Out-Null

# Three independent devnet nodes, three data directories, one shared genesis.
#   node1: no seeds     node2: seeds node1     node3: seeds node2 (must DISCOVER node1)
$nodes = @(
    @{ Name = "node1"; Api = 18201; P2p = 18211; Seeds = "" },
    @{ Name = "node2"; Api = 18202; P2p = 18212; Seeds = "127.0.0.1:18211" },
    @{ Name = "node3"; Api = 18203; P2p = 18213; Seeds = "127.0.0.1:18212" }
)
$procs = @()
function Get-Status($api) { Invoke-RestMethod "http://127.0.0.1:$api/api/status" }
function Show-Table {
    foreach ($n in $nodes) {
        $s = Get-Status $n.Api
        "{0}  api:{1}  height:{2}  peers:{3}  mempool:{4}  tip:{5}" -f $n.Name, $n.Api, $s.height, $s.peer_count, $s.mempool_size, $s.latest_hash.Substring(0, 20)
    }
}
function Wait-Until([scriptblock]$Cond, [string]$What, [int]$Seconds = 60) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) { if (& $Cond) { return }; Start-Sleep -Milliseconds 300 }
    Show-Table
    throw "Timed out waiting for: $What"
}
function All-Same-Tip($height) {
    $tips = @(); foreach ($n in $nodes) { $s = Get-Status $n.Api; if ($s.height -ne $height) { return $false }; $tips += $s.latest_hash }
    return (($tips | Select-Object -Unique).Count -eq 1)
}

try {
    foreach ($n in $nodes) {
        $env:MINEAI_NETWORK = "devnet"
        $env:MINEAI_DATA_DIR = Join-Path $root $n.Name
        $env:MINEAI_PORT = "$($n.Api)"
        $env:MINEAI_P2P_PORT = "$($n.P2p)"
        $env:MINEAI_SEEDS = $n.Seeds
        Write-Host "Starting $($n.Name)  API :$($n.Api)  P2P :$($n.P2p)  seeds='$($n.Seeds)'" -ForegroundColor Cyan
        $procs += Start-Process -FilePath $py -ArgumentList "-m", "mineai.node" -PassThru -WindowStyle Hidden `
            -RedirectStandardError (Join-Path $root "$($n.Name).log")
    }
    foreach ($n in $nodes) { Wait-Until { try { (Invoke-RestMethod "http://127.0.0.1:$($n.Api)/api/health").status -eq "ok" } catch { $false } } "$($n.Name) API" }

    Write-Host "`nWaiting for the network to form (node3 must discover node1 through node2)..." -ForegroundColor Cyan
    Wait-Until { (Get-Status 18203).peer_count -ge 2 -and (Get-Status 18201).peer_count -ge 2 } "full mesh"
    Show-Table

    # Wallets (throwaway password, never printed). The env var is set only after the nodes started.
    $env:MINEAI_WALLET_PASSWORD = [Convert]::ToBase64String((1..24 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])
    foreach ($w in "alice", "bob") { Invoke-Checked $py -m mineai.wallet create --wallet "$root\$w.wallet.json" | Out-Null }
    $alice = (& $py -m mineai.wallet address --wallet "$root\alice.wallet.json" | Select-Object -Last 1).Trim()
    $bob = (& $py -m mineai.wallet address --wallet "$root\bob.wallet.json" | Select-Object -Last 1).Trim()

    Write-Host "`nMining 4 blocks on node1 only..." -ForegroundColor Yellow
    Invoke-Checked $py -m mineai.miner --address $alice --node "http://127.0.0.1:18201" --blocks 4 --threads 2 | Out-Null
    Wait-Until { All-Same-Tip 4 } "all nodes at height 4 with the same tip"
    Show-Table

    Write-Host "`nSending 5 MAI Alice -> Bob through node3's API (transaction must reach the others)..." -ForegroundColor Yellow
    Invoke-Checked $py -m mineai.wallet send --wallet "$root\alice.wallet.json" --node "http://127.0.0.1:18203" --to $bob --amount 5 --yes | Out-Null
    Wait-Until { ($nodes | ForEach-Object { (Get-Status $_.Api).mempool_size -eq 1 }) -notcontains $false } "transaction in every mempool"
    Show-Table

    Write-Host "`nMining the confirming block on node2 (a different node)..." -ForegroundColor Yellow
    Invoke-Checked $py -m mineai.miner --address $alice --node "http://127.0.0.1:18202" --blocks 1 --threads 2 | Out-Null
    Wait-Until { All-Same-Tip 5 } "all nodes at height 5 with the same tip"
    Show-Table

    foreach ($n in $nodes) {
        $bal = (Invoke-RestMethod "http://127.0.0.1:$($n.Api)/api/account/$bob").balance
        if ($bal -ne 5000000) { throw "$($n.Name): Bob's balance is $bal, expected 5000000 atomic units" }
    }
    Write-Host "`nPASS: three independent nodes share one genesis, relayed a transaction and blocks, and agree on the same chain tip." -ForegroundColor Green
}
finally {
    Remove-Item Env:\MINEAI_WALLET_PASSWORD, Env:\MINEAI_SEEDS, Env:\MINEAI_P2P_PORT, Env:\MINEAI_PORT, Env:\MINEAI_DATA_DIR -ErrorAction SilentlyContinue
    foreach ($p in $procs) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force } }
}
