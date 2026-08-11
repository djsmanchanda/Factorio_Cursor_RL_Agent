# Path: scripts/manage_wsl_training_worker.ps1
# Purpose: Manage isolated WSL Factorio training workers without touching the real-base runtime.

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("bootstrap", "configure", "deploy", "start", "stop", "status")]
    [string]$Action,
    [string]$Distro = "Ubuntu",
    [string]$Archive = "C:\Users\djsma\Downloads\factorio-headless_linux_2.0.77.tar.xz",
    [string]$SourceSave = "C:\Users\djsma\AppData\Local\Factorio-training-01\saves\training-01.zip",
    [string]$BridgeRootBase = "$env:LOCALAPPDATA\Factorio-training-wsl",
    [ValidateRange(1, 8)]
    [int]$WorkerCount = 1,
    [ValidateRange(1, 40)]
    [int]$SlotsPerWorker = 4
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$workerScript = Join-Path $repoRoot "scripts\wsl\training_worker.sh"

function WorkerSuffix([int]$Index) { return "{0:D2}" -f $Index }
function WorkerBridgeRoot([int]$Index) { return "$BridgeRootBase-$(WorkerSuffix $Index)" }
function GamePort([int]$Index) { return 35000 + $Index }
function RconPort([int]$Index) { return 28000 + $Index }

function ConvertTo-WslPath {
    param([Parameter(Mandatory)][string]$WindowsPath)
    (& wsl.exe -d $Distro -- wslpath -a $WindowsPath.Replace("\", "/")).Trim()
}

function Assert-TrainingPortsAvailable {
    for ($index = 1; $index -le $WorkerCount; $index++) {
        foreach ($port in @((GamePort $index), (RconPort $index))) {
            if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
                throw "Training port $port is already in use; stop the existing WSL worker first."
            }
        }
        if (Get-NetUDPEndpoint -LocalPort (GamePort $index) -ErrorAction SilentlyContinue) {
            throw "Training game port $(GamePort $index) is already in use; stop the existing WSL worker first."
        }
    }
}


function Invoke-Worker {
    param([Parameter(Mandatory)][int]$Index, [Parameter(Mandatory)][string]$WorkerAction, [string[]]$Arguments = @())
    $workerScriptWsl = ConvertTo-WslPath $workerScript
    & wsl.exe -d $Distro -- bash $workerScriptWsl $WorkerAction (WorkerSuffix $Index) @Arguments
    if ($LASTEXITCODE -ne 0) { throw "WSL worker $(WorkerSuffix $Index) action failed: $WorkerAction" }
}

function Write-WorkerConfig {
    $output = Join-Path $repoRoot "training-workers-wsl.json"
    $workers = foreach ($index in 1..$WorkerCount) {
        $bridgeRoot = WorkerBridgeRoot $index
        foreach ($slot in 1..$SlotsPerWorker) {
            [ordered]@{
                worker_id = "training-wsl-$(WorkerSuffix $index)-slot-$(WorkerSuffix $slot)"
                # Slots are concurrent surfaces in this one explicit Factorio process.
                instance_id = "factorio-training-wsl-$(WorkerSuffix $index)"
                host = "127.0.0.1"
                game_port = GamePort $index
                rcon_port = RconPort $index
                script_output = (Join-Path $bridgeRoot "script-output").Replace("\", "/")
                surface_prefix = "training/"
                force_prefix = "training-"
            }
        }
    }
    $payload = [ordered]@{ workers = @($workers) }
    [IO.File]::WriteAllText($output, ($payload | ConvertTo-Json -Depth 4) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    Write-Host "Wrote ignored local worker config for $WorkerCount Factorio runtime(s) and $($workers.Count) concurrent slot(s): $output"
}

if (-not (Test-Path -LiteralPath $workerScript -PathType Leaf)) {
    throw "WSL training worker script is missing: $workerScript"
}

switch ($Action) {
    "bootstrap" {
        if (-not (Test-Path -LiteralPath $SourceSave -PathType Leaf)) { throw "Bootstrap input is missing: $SourceSave" }
        for ($index = 1; $index -le $WorkerCount; $index++) {
            $bridgeRoot = WorkerBridgeRoot $index
            New-Item -ItemType Directory -Path $bridgeRoot -Force | Out-Null
            Invoke-Worker $index "bootstrap" @(
                (ConvertTo-WslPath $Archive), (ConvertTo-WslPath $SourceSave),
                (ConvertTo-WslPath $repoRoot), (ConvertTo-WslPath $bridgeRoot)
            )

        }
        Write-WorkerConfig
    }
    "configure" {
        Write-WorkerConfig
    }
    "deploy" {
        for ($index = 1; $index -le $WorkerCount; $index++) {
            Invoke-Worker $index "deploy" @((ConvertTo-WslPath $repoRoot))
        }
    }
    "start" {
        Assert-TrainingPortsAvailable
        for ($index = 1; $index -le $WorkerCount; $index++) { Invoke-Worker $index "start" }
    }
    "stop" {
        for ($index = 1; $index -le $WorkerCount; $index++) { Invoke-Worker $index "stop" }
    }
    "status" {
        for ($index = 1; $index -le $WorkerCount; $index++) { Invoke-Worker $index "status" }
    }
}