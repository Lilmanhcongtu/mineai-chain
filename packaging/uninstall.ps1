param(
    [string]$Target = (Join-Path $env:LOCALAPPDATA "Programs\MineAI")
)
# Removes the program only. Wallet files and chain data are never touched.
$ErrorActionPreference = "Stop"
if (-not (Test-Path (Join-Path $Target "mineai\mineai.exe"))) { throw "No MineAI installation found in $Target" }
Remove-Item -Recurse -Force $Target
$current = [Environment]::GetEnvironmentVariable("Path", "User")
if ($current -and (($current -split ";") -contains $Target)) {
    [Environment]::SetEnvironmentVariable("Path", (($current -split ";" | Where-Object { $_ -ne $Target }) -join ";"), "User")
    Write-Host "Removed $Target from your user PATH."
}
Write-Host "Uninstalled. Your wallets and chain data (%USERPROFILE%\.mineai) were NOT deleted." -ForegroundColor Green
