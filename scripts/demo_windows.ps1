$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot\_common.ps1"
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run .\scripts\setup_windows.ps1 first." }
$py = (Resolve-Path ".venv\Scripts\python.exe").Path
$demo = Join-Path $PWD "demo"
New-Item -ItemType Directory -Force -Path $demo | Out-Null
$env:MINEAI_NETWORK = "devnet"
$env:MINEAI_DATA_DIR = Join-Path $demo "chain"
$env:MINEAI_PORT = "18099"
$node = "http://127.0.0.1:18099"
# Throwaway demo password, random per run, never printed or stored.
$env:MINEAI_WALLET_PASSWORD = [Convert]::ToBase64String((1..24 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])

Write-Host "Starting isolated demo node on $node ..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $py -ArgumentList "-m", "mineai.node" -PassThru -WindowStyle Hidden
try {
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        try { Invoke-RestMethod "$node/api/health" | Out-Null; $ready = $true; break } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "Demo node did not become ready." }
    foreach ($n in "alice", "bob") {
        if (-not (Test-Path "$demo\$n.wallet.json")) { Invoke-Checked $py -m mineai.wallet create --wallet "$demo\$n.wallet.json" }
    }
    $alice = (& $py -m mineai.wallet address --wallet "$demo\alice.wallet.json" | Select-Object -Last 1).Trim()
    $bob = (& $py -m mineai.wallet address --wallet "$demo\bob.wallet.json" | Select-Object -Last 1).Trim()
    Write-Host "Mining 4 blocks to Alice (coinbase maturity is 3 blocks)..." -ForegroundColor Yellow
    Invoke-Checked $py -m mineai.miner --address $alice --node $node --blocks 4 --threads 2
    Invoke-Checked $py -m mineai.wallet balance --wallet "$demo\alice.wallet.json" --node $node
    Write-Host "Sending 5 MAI Alice -> Bob..." -ForegroundColor Yellow
    Invoke-Checked $py -m mineai.wallet send --wallet "$demo\alice.wallet.json" --node $node --to $bob --amount 5 --yes
    Invoke-Checked $py -m mineai.miner --address $alice --node $node --blocks 1 --threads 2
    Write-Host "Bob:" -ForegroundColor Green
    Invoke-Checked $py -m mineai.wallet balance --wallet "$demo\bob.wallet.json" --node $node
    Write-Host "Demo complete (demo files are in .\demo, which is git-ignored)." -ForegroundColor Green
}
finally {
    Remove-Item Env:\MINEAI_WALLET_PASSWORD -ErrorAction SilentlyContinue
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}
