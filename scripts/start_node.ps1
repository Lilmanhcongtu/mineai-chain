$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot\_common.ps1"
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run .\scripts\setup_windows.ps1 first." }
if (-not $env:MINEAI_NETWORK) { $env:MINEAI_NETWORK = "devnet" }
if (-not $env:MINEAI_HOST) { $env:MINEAI_HOST = "127.0.0.1" }
Write-Host "MineAI node starting on network '$($env:MINEAI_NETWORK)' (Ctrl+C to stop)" -ForegroundColor Cyan
Invoke-Checked .\.venv\Scripts\python.exe -m mineai.node
