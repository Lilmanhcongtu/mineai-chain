param([switch]$SkipTests)
# Builds the Windows package: dist\MineAI-<version>-windows\ and dist\MineAI-<version>-windows.zip (+ checksums).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot\_common.ps1"
Write-Host "=== MineAI Windows package build ===" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.10+ was not found." }

$venv = ".build-venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) { Invoke-Checked python -m venv $venv }
$py = (Resolve-Path "$venv\Scripts\python.exe").Path
Invoke-Checked $py -m pip install --upgrade pip
Invoke-Checked $py -m pip install ".[dev]" "pyinstaller>=6"
$version = (& $py -c "import mineai; print(mineai.__version__)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Cannot read the version." }
if (-not $SkipTests) { Invoke-Checked $py -m pytest -q -x -p no:cacheprovider }

$name = "MineAI-$version-windows"
$out = Join-Path "dist" $name
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
if (Test-Path "dist\$name.zip") { Remove-Item -Force "dist\$name.zip" }
if (Test-Path "build\pyi") { Remove-Item -Recurse -Force "build\pyi" }
New-Item -ItemType Directory -Force -Path $out | Out-Null

Invoke-Checked $py -m PyInstaller --noconfirm --clean --onedir --name mineai `
    --distpath $out --workpath "build\pyi" --specpath "build\pyi" `
    --paths . `
    --add-data "$((Resolve-Path 'mineai\templates').Path);mineai\templates" `
    --collect-submodules uvicorn --collect-submodules mineai `
    --hidden-import httptools --hidden-import websockets `
    --exclude-module tkinter --exclude-module pytest `
    "packaging\mineai_entry.py"

# wrapper scripts, one per tool
foreach ($tool in "node", "wallet", "miner") {
    Set-Content -Path (Join-Path $out "mineai-$tool.cmd") -Encoding ASCII -Value "@echo off`r`n`"%~dp0mineai\mineai.exe`" $tool %*"
}
Copy-Item "packaging\README-WINDOWS.txt", "packaging\install.ps1", "packaging\uninstall.ps1" $out
New-Item -ItemType Directory -Force -Path (Join-Path $out "docs") | Out-Null
Copy-Item README.md, PROTOCOL.md, TOKENOMICS.md, NETWORK.md, SECURITY.md, CONTRIBUTING.md, "docs\RANDOMX_EVALUATION.md" (Join-Path $out "docs")

# checksums of every file, then of the zip
$files = Get-ChildItem $out -Recurse -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } | Sort-Object FullName
$lines = foreach ($f in $files) {
    $rel = $f.FullName.Substring((Resolve-Path $out).Path.Length + 1).Replace("\", "/")
    "{0}  {1}" -f (Get-FileHash $f.FullName -Algorithm SHA256).Hash.ToLower(), $rel
}
Set-Content -Path (Join-Path $out "SHA256SUMS.txt") -Value $lines -Encoding ASCII
Compress-Archive -Path $out -DestinationPath "dist\$name.zip" -CompressionLevel Optimal
$zipHash = (Get-FileHash "dist\$name.zip" -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "dist\$name.zip.sha256" -Value "$zipHash  $name.zip" -Encoding ASCII
Write-Host "`nBuilt $out" -ForegroundColor Green
Write-Host "Zip:    dist\$name.zip  ($([math]::Round((Get-Item "dist\$name.zip").Length / 1MB, 1)) MB)"
Write-Host "SHA-256 $zipHash"
