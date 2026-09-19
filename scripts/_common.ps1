# Shared helpers. PowerShell does NOT stop on a failing native command even with
# $ErrorActionPreference = "Stop", so every native call goes through Invoke-Checked.
# (Deliberately a simple function using $args: an advanced function would swallow
# arguments such as -e / -m as PowerShell parameter prefixes.)
function Invoke-Checked {
    $exe = $args[0]
    $rest = @()
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit code $LASTEXITCODE): $exe $($rest -join ' ')" }
}
