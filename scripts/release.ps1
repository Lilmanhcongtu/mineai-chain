param([switch]$SkipTests)
# Builds every release artifact into release\<version>\ : Windows package zip, source archive (from git, tracked files only),
# SHA256SUMS.txt and MANIFEST.txt.  Artifacts are UNSIGNED.  Nothing is uploaded or published.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot\_common.ps1"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git is required for the source archive." }
if (git status --porcelain) { throw "The working tree has uncommitted changes; commit first so the archive matches the tag." }

$args2 = @(); if ($SkipTests) { $args2 += "-SkipTests" }
& "$PSScriptRoot\build_windows.ps1" @args2
if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { throw "Windows build failed." }

$py = (Resolve-Path ".build-venv\Scripts\python.exe").Path
$version = (& $py -c "import mineai; print(mineai.__version__)").Trim()
$out = "release\$version"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force -Path $out | Out-Null

Copy-Item "dist\MineAI-$version-windows.zip" $out
$src = "MineAI-$version-source.zip"
Invoke-Checked git archive --format=zip --prefix="MineAI-$version/" -o "$out\$src" HEAD
$commit = (git rev-parse HEAD).Trim()

$lines = foreach ($f in Get-ChildItem $out -File | Where-Object { $_.Name -notin "SHA256SUMS.txt", "MANIFEST.txt" } | Sort-Object Name) {
    "{0}  {1}" -f (Get-FileHash $f.FullName -Algorithm SHA256).Hash.ToLower(), $f.Name
}
Set-Content -Path "$out\SHA256SUMS.txt" -Value $lines -Encoding ASCII
Set-Content -Path "$out\MANIFEST.txt" -Encoding ASCII -Value @(
    "MineAI $version (public testnet release candidate, UNSIGNED)",
    "git commit: $commit",
    "network: mineai-testnet-v1 (TMAI)",
    "built: $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) on $env:COMPUTERNAME"
)
Copy-Item docs\RELEASE_NOTES.md, docs\INSTALL.md, docs\KNOWN_LIMITATIONS.md $out
Write-Host "`nRelease artifacts in $out" -ForegroundColor Green
Get-Content "$out\SHA256SUMS.txt"
