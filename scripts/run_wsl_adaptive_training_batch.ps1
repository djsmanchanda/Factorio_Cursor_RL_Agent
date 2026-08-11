# Path: scripts/run_wsl_adaptive_training_batch.ps1
# Purpose: Run the UPS-gated adaptive RL controller against the WSL training worker.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$TrainingArguments,
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$workerConfig = Join-Path $repoRoot "training-workers-wsl.json"
$secretLinuxPath = (& wsl.exe -d $Distro -- bash -lc 'printf %s "$HOME/factorio-training-01/rcon-password"').Trim()
$secretFile = "\\wsl$\$Distro$($secretLinuxPath.Replace('/', '\'))"

foreach ($path in @($workerConfig, $secretFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "WSL training prerequisite is missing: $path. Run manage_wsl_training_worker.ps1 bootstrap first."
    }
}

& python (Join-Path $repoRoot "tools\run_adaptive_training_batch.py") --workers $workerConfig `
    --rcon-secret-file $secretFile @TrainingArguments
exit $LASTEXITCODE
