# Shared helpers.
# Windows PowerShell 5.1 has two traps with native commands under $ErrorActionPreference = "Stop":
#   1. a failing exit code does NOT stop the script, and
#   2. ANYTHING a tool writes to stderr (progress, warnings) becomes a terminating error, even on success.
# Invoke-Checked handles both: stderr is shown but harmless, and only the real exit code decides.
# (Deliberately a simple function using $args: an advanced function would swallow arguments such as -e / -m
# as PowerShell parameter prefixes.)
function Invoke-Checked {
    $exe = $args[0]
    $rest = @()
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $exe @rest 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    if ($code -ne 0) { throw "Command failed (exit code $code): $exe $($rest -join ' ')" }
}
