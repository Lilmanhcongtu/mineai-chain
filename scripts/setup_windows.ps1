$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot\_common.ps1"
Write-Host "=== MineAI Windows Setup (devnet/testnet software, not production) ===" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python 3.10+ and enable 'Add Python to PATH'."
}
Invoke-Checked python -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ required'; print(sys.version)"
if (-not (Test-Path ".venv\Scripts\python.exe")) { Invoke-Checked python -m venv .venv }
Invoke-Checked .\.venv\Scripts\python.exe -m pip install --upgrade pip
Invoke-Checked .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Invoke-Checked .\.venv\Scripts\python.exe -c "import mineai, fastapi, cryptography, httpx; print('MineAI', mineai.__version__, 'import check OK')"

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Start node: .\scripts\start_node.ps1"
Write-Host "Explorer:   http://127.0.0.1:8080"
