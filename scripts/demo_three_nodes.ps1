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
$procs = @{}
$logCounter = 0
function Start-NodeProc($n, [string]$seeds, [string]$p2pEnabled = "1") {
    $script:logCounter++
    $env:MINEAI_NETWORK = "devnet"
    $env:MINEAI_DATA_DIR = Join-Path $root $n.Name
    $env:MINEAI_PORT = "$($n.Api)"
    $env:MINEAI_P2P_PORT = "$($n.P2p)"
    $env:MINEAI_SEEDS = $seeds
    $env:MINEAI_P2P = $p2pEnabled
    $procs[$n.Name] = Start-Process -FilePath $py -ArgumentList "-m", "mineai.node" -PassThru -WindowStyle Hidden `
        -RedirectStandardError (Join-Path $root "$($n.Name).$($script:logCounter).log")
}
function Stop-NodeProc($name) {
    $p = $procs[$name]
    if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force; $p.WaitForExit() }
}
function Get-Status($api) { Invoke-RestMethod "http://127.0.0.1:$api/api/status" }
function Show-Table {
    foreach ($n in $nodes) {
        try {
            $s = Get-Status $n.Api
            "{0}  height:{1}  peers:{2}  mempool:{3}  reorgs:{4}  tip:{5}" -f $n.Name, $s.height, $s.peer_count, $s.mempool_size, $s.reorgs_since_start, $s.latest_hash.Substring(0, 20)
        } catch { "{0}  (offline)" -f $n.Name }
    }
}
function Wait-Until([scriptblock]$Cond, [string]$What, [int]$Seconds = 60) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) { if (& $Cond) { return }; Start-Sleep -Milliseconds 300 }
    Show-Table
    throw "Timed out waiting for: $What"
}
function Wait-Api($n) { Wait-Until { try { (Invoke-RestMethod "http://127.0.0.1:$($n.Api)/api/health").status -eq "ok" } catch { $false } } "$($n.Name) API" }
function All-Same-Tip($height) {
    $tips = @(); foreach ($n in $nodes) { $s = Get-Status $n.Api; if ($s.height -ne $height) { return $false }; $tips += $s.latest_hash }
    return (($tips | Select-Object -Unique).Count -eq 1)
}

try {
    foreach ($n in $nodes) {
        Write-Host "Starting $($n.Name)  API :$($n.Api)  P2P :$($n.P2p)  seeds='$($n.Seeds)'" -ForegroundColor Cyan
        Start-NodeProc $n $n.Seeds
    }
    foreach ($n in $nodes) { Wait-Api $n }

    Write-Host "`nWaiting for the network to form (node3 must discover node1 through node2)..." -ForegroundColor Cyan
    Wait-Until { (Get-Status 18203).peer_count -ge 2 -and (Get-Status 18201).peer_count -ge 2 } "full mesh"
    Show-Table

    # Wallets (throwaway password, never printed). The env var is set only after the nodes started.
    $env:MINEAI_WALLET_PASSWORD = [Convert]::ToBase64String((1..24 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])
    foreach ($w in "alice", "bob") { Invoke-Checked $py -m mineai.wallet create --wallet "$root\$w.wallet.json" | Out-Null }
    $alice = (& $py -m mineai.wallet address --wallet "$root\alice.wallet.json" | Select-Object -Last 1).Trim()
    $bob = (& $py -m mineai.wallet address --wallet "$root\bob.wallet.json" | Select-Object -Last 1).Trim()
    $loner = (& $py -m mineai.wallet address --wallet "$root\bob.wallet.json" | Select-Object -Last 1).Trim()

    Write-Host "`n[1] Mining 4 blocks on node1 only..." -ForegroundColor Yellow
    Invoke-Checked $py -m mineai.miner --address $alice --node "http://127.0.0.1:18201" --blocks 4 --threads 2 | Out-Null
    Wait-Until { All-Same-Tip 4 } "all nodes at height 4 with the same tip"
    Show-Table

    Write-Host "`n[2] Sending 5 MAI Alice -> Bob through node3's API (transaction must reach the others)..." -ForegroundColor Yellow
    Invoke-Checked $py -m mineai.wallet send --wallet "$root\alice.wallet.json" --node "http://127.0.0.1:18203" --to $bob --amount 5 --yes | Out-Null
    Wait-Until { ($nodes | ForEach-Object { (Get-Status $_.Api).mempool_size -eq 1 }) -notcontains $false } "transaction in every mempool"

    Write-Host "`n[3] Mining the confirming block on node2 (a different node)..." -ForegroundColor Yellow
    Invoke-Checked $py -m mineai.miner --address $alice --node "http://127.0.0.1:18202" --blocks 1 --threads 2 | Out-Null
    Wait-Until { All-Same-Tip 5 } "all nodes at height 5 with the same tip"
    Show-Table

    # ---- a REAL chain reorganization between separate processes -------------------------------------------
    Write-Host "`n[4] Partition: node3 goes offline and mines a private 2-block fork (P2P disabled)..." -ForegroundColor Yellow
    Stop-NodeProc "node3"
    Start-NodeProc $nodes[2] "" "0"
    Wait-Api $nodes[2]
    Invoke-Checked $py -m mineai.miner --address $loner --node "http://127.0.0.1:18203" --blocks 2 --threads 2 | Out-Null
    $forkTip = (Get-Status 18203).latest_hash
    Write-Host "    node3 private fork: height $((Get-Status 18203).height), tip $($forkTip.Substring(0,20))"

    Write-Host "`n[5] Meanwhile node1/node2 build a longer chain (4 more blocks)..." -ForegroundColor Yellow
    Invoke-Checked $py -m mineai.miner --address $alice --node "http://127.0.0.1:18201" --blocks 4 --threads 2 | Out-Null
    Wait-Until { (Get-Status 18201).height -eq 9 -and (Get-Status 18202).height -eq 9 } "node1/node2 at height 9"
    $mainTip = (Get-Status 18201).latest_hash
    if ($forkTip -eq $mainTip) { throw "expected different tips before the partition heals" }

    Write-Host "`n[6] Partition heals: node3 restarts with P2P and must reorganize onto the heavier chain..." -ForegroundColor Yellow
    Stop-NodeProc "node3"
    Start-NodeProc $nodes[2] "127.0.0.1:18212" "1"
    Wait-Api $nodes[2]
    Wait-Until { All-Same-Tip 9 } "all nodes at height 9 with the same tip after the reorganization"
    Show-Table
    $logs = Get-ChildItem $root -Filter "node3.*.log" | Sort-Object Name
    $reorgLines = $logs | ForEach-Object { Select-String -Path $_.FullName -Pattern '"event":"reorganization"' } | ForEach-Object { $_.Line }
    if (-not $reorgLines) { throw "node3 did not log a reorganization" }
    Write-Host "    node3 log: $($reorgLines | Select-Object -First 1)"

    foreach ($n in $nodes) {
        $bal = (Invoke-RestMethod "http://127.0.0.1:$($n.Api)/api/account/$bob").balance
        # Bob received 5 MAI by transfer; the private-fork coinbases (2 x 25) went to "$loner" == Bob's address
        # and must have DISAPPEARED with the abandoned branch, leaving exactly the 5 MAI transfer.
        if ($bal -ne 5000000) { throw "$($n.Name): Bob's balance is $bal, expected 5000000 (abandoned-branch coinbases must be gone)" }
    }
    Write-Host "`nPASS: three independent processes share one genesis, relayed a transaction and blocks, survived a partition," -ForegroundColor Green
    Write-Host "      and node3 abandoned its private fork (coinbases reverted) to converge on the heaviest chain." -ForegroundColor Green
}
finally {
    Remove-Item Env:\MINEAI_WALLET_PASSWORD, Env:\MINEAI_SEEDS, Env:\MINEAI_P2P_PORT, Env:\MINEAI_P2P, Env:\MINEAI_PORT, Env:\MINEAI_DATA_DIR -ErrorAction SilentlyContinue
    foreach ($p in $procs.Values) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force } }
}
