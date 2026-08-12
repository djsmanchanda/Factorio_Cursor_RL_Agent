# Path: scripts/queue_overnight_training.ps1
# Purpose: Queue isolated RL batches without interrupting the currently running controller.

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [int]$WaitForPid,
    [string]$Distro = "Ubuntu",
    [int]$PreviousWorkerCount = 5,
    [int]$WorkerCount = 20,
    [int]$SlotsPerWorker = 16,
    [double]$StaggerSeconds = 2
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $repoRoot "data\training-overnight"
$logPath = Join-Path $logRoot "overnight.log"
$workerManager = Join-Path $repoRoot "scripts\manage_wsl_training_worker.ps1"
$adaptiveRunner = Join-Path $repoRoot "scripts\run_wsl_adaptive_training_batch.ps1"
$workerConfig = Join-Path $repoRoot "training-workers-wsl.json"
$checkpoint = Join-Path $logRoot "policy.json"
$database = Join-Path $logRoot "experience.db"
$liveDirectory = Join-Path $logRoot "live"

New-Item -ItemType Directory -Path $logRoot, $liveDirectory -Force | Out-Null

function Write-QueueLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format o), $Message
    Add-Content -LiteralPath $logPath -Value $line
    Write-Host $line
}

function Invoke-PowerShellStep([string]$Label, [string]$Script, [string[]]$Arguments) {
    Write-QueueLog "START $Label"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script @Arguments 2>&1 |
        ForEach-Object { Add-Content -LiteralPath $logPath -Value ("[{0}] {1}" -f (Get-Date -Format o), $_); Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
    Write-QueueLog "DONE $Label"
}

Write-QueueLog "Waiting for current RL controller PID $WaitForPid to exit; no live process will be interrupted."
while (Get-Process -Id $WaitForPid -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds 30
}
Write-QueueLog "Current controller exited; preparing the next isolated training sequence."

Invoke-PowerShellStep "stop previous five WSL workers" $workerManager @(
    "-Action", "stop", "-Distro", $Distro, "-WorkerCount", $PreviousWorkerCount
)
Invoke-PowerShellStep "bootstrap twenty WSL workers" $workerManager @(
    "-Action", "bootstrap", "-Distro", $Distro, "-WorkerCount", $WorkerCount,
    "-SlotsPerWorker", $SlotsPerWorker, "-StaggerSeconds", $StaggerSeconds
)
Invoke-PowerShellStep "start twenty WSL workers" $workerManager @(
    "-Action", "start", "-Distro", $Distro, "-WorkerCount", $WorkerCount,
    "-SlotsPerWorker", $SlotsPerWorker, "-StaggerSeconds", $StaggerSeconds
)

$common = @(
    "--workers", $workerConfig, "--database", $database, "--checkpoint", $checkpoint,
    "--live-directory", $liveDirectory, "--initial-slots", "8", "--minimum-slots", "1",
    "--maximum-slots", "16", "--step", "1", "--episodes-per-slot", "1",
    "--episodes-per-policy", "100", "--minimum-ups-p95", "50", "--minimum-ups-p98", "45",
    "--stability-window-seconds", "300"
)

Invoke-PowerShellStep "baseline adaptive batch (100 scenarios x 20 attempts)" $adaptiveRunner @(
    "-Distro", $Distro, "--count", "100", "--start-seed", "20000",
    "--attempts-per-scenario", "20" + $common
)

foreach ($phase in @(
    @{ Name = "demand-3-per-second"; Rate = "3"; Seed = "30000" },
    @{ Name = "demand-10-per-second"; Rate = "10"; Seed = "40000" },
    @{ Name = "demand-30-per-second"; Rate = "30"; Seed = "50000" }
)) {
    Invoke-PowerShellStep "$($phase.Name) stress batch (100 tests)" $adaptiveRunner @(
        "-Distro", $Distro, "--count", "100", "--start-seed", $phase.Seed,
        "--attempts-per-scenario", "1", "--target-rates-per-second", $phase.Rate + $common
    )
}

Write-QueueLog "Overnight RL sequence completed. Workers remain running for inspection; controller is stopped."
