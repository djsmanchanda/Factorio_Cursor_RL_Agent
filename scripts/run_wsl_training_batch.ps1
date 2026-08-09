# Path: scripts/run_wsl_training_batch.ps1
# Purpose: Run the Windows training controller against the WSL worker without manual credential entry.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$TrainingArguments
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$workerConfig = Join-Path $repoRoot "training-workers-wsl.json"
$secretFile = "$env:LOCALAPPDATA\Factorio-training-wsl-01\rcon-password"

foreach ($path in @($workerConfig, $secretFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "WSL training prerequisite is missing: $path. Run manage_wsl_training_worker.ps1 bootstrap first."
    }
}

& python (Join-Path $repoRoot "tools\run_training_batch.py") --workers $workerConfig `
    --rcon-secret-file $secretFile @TrainingArguments
exit $LASTEXITCODE
