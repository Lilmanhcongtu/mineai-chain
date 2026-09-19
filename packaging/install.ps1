param(
    [string]$Target = (Join-Path $env:LOCALAPPDATA "Programs\MineAI"),
    [switch]$NoPath
)
# Per-user install. No administrator rights, no services, no scheduled tasks, no auto-start.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $here "mineai\mineai.exe"))) { throw "mineai\mineai.exe not found next to this script." }
if (Test-Path $Target) {
    if (Test-Path (Join-Path $Target "mineai\mineai.exe")) { Write-Host "Updating existing installation in $Target" }
    elseif ((Get-ChildItem $Target -Force | Measure-Object).Count -gt 0) { throw "Target folder exists and is not a MineAI installation: $Target" }
}
New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item -Path (Join-Path $here "mineai") -Destination $Target -Recurse -Force
foreach ($f in "mineai-node.cmd", "mineai-wallet.cmd", "mineai-miner.cmd", "README-WINDOWS.txt", "uninstall.ps1", "SHA256SUMS.txt") {
    if (Test-Path (Join-Path $here $f)) { Copy-Item (Join-Path $here $f) $Target -Force }
}
if (Test-Path (Join-Path $here "docs")) { Copy-Item (Join-Path $here "docs") $Target -Recurse -Force }
Write-Host "Installed MineAI to $Target" -ForegroundColor Green

if (-not $NoPath) {
    $answer = Read-Host "Add $Target to your user PATH so 'mineai-node', 'mineai-wallet' and 'mineai-miner' work anywhere? [y/N]"
    if ($answer -match '^(y|yes)$') {
        $current = [Environment]::GetEnvironmentVariable("Path", "User")
        if (($current -split ";") -notcontains $Target) {
            [Environment]::SetEnvironmentVariable("Path", (($current.TrimEnd(";") + ";" + $Target).TrimStart(";")), "User")
            Write-Host "Added to your user PATH. Open a new terminal to use it."
        }
    }
}
Write-Host "Nothing has been started. Run mineai-node.cmd when you want to start a node."
