# Path: scripts/manage_windows_training_worker.ps1
# Purpose: Provision and manage one isolated native-Windows Factorio training runtime.

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("bootstrap", "configure", "deploy", "start", "stop", "status")]
    [string]$Action,
    [string]$ServerData = "$env:LOCALAPPDATA\Factorio-training-win-01",
    [string]$SourceSave = "$env:LOCALAPPDATA\Factorio-training-01\saves\training-01.zip",
    [string]$FactorioExe = "E:\Games\Factorio\bin\x64\factorio.exe",
    [string]$ReadData = "E:\Games\Factorio\data",
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [ValidateRange(1, 65535)]
    [int]$GamePort = 35006,
    [ValidateRange(1, 65535)]
    [int]$RconPort = 28006,
    [ValidateRange(1, 80)]
    [int]$SlotsPerWorker = 16,
    [string]$ConfigOutput = "training-workers-windows.json",
    [string]$RconPassword = $env:FACTORIO_TRAINING_RCON_PASSWORD
)

$ErrorActionPreference = "Stop"

function Write-Utf8NoBom {
    param([Parameter(Mandatory)][string]$LiteralPath, [Parameter(Mandatory)][string]$Content)
    [IO.File]::WriteAllText($LiteralPath, $Content, [Text.UTF8Encoding]::new($false))
}

function Require-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file is missing: $Path"
    }
}

function Require-Directory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Required directory is missing: $Path"
    }
}

function Paths {
    [ordered]@{
        Saves = Join-Path $ServerData "saves"
        Mods = Join-Path $ServerData "mods"
        Logs = Join-Path $ServerData "logs"
        ScriptOutput = Join-Path $ServerData "script-output"
        Config = Join-Path $ServerData "config.ini"
        Settings = Join-Path $ServerData "server-settings.json"
        Save = Join-Path $ServerData "saves\training-01.zip"
        Pid = Join-Path $ServerData "factorio.pid"
        Stdout = Join-Path $ServerData "logs\factorio-stdout.log"
        Stderr = Join-Path $ServerData "logs\factorio-stderr.log"
    }
}

function Assert-StaticInputs {
    Require-File $FactorioExe
    Require-Directory $ReadData
    Require-File $SourceSave
    Require-File (Join-Path $RepoRoot "factorio_training_lab\control.lua")
    Require-File (Join-Path $RepoRoot "factorio_mod\control.lua")
}

function Ensure-Directories {
    $p = Paths
    foreach ($directory in @($ServerData, $p.Saves, $p.Mods, $p.Logs, $p.ScriptOutput)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
}

function Write-ServerFiles {
    $p = Paths
    if (-not (Test-Path -LiteralPath $p.Config -PathType Leaf)) {
        Write-Utf8NoBom $p.Config "[path]`nread-data=$ReadData`nwrite-data=$ServerData`n"
    }
    if (-not (Test-Path -LiteralPath $p.Settings -PathType Leaf)) {
        $settings = [ordered]@{
            name = "Factorio RL Windows Training Worker"
            description = "Private isolated native-Windows Factorio RL training worker"
            visibility = [ordered]@{ public = $false; lan = $true }
            game_password = ""
            require_user_verification = $false
            auto_pause = $false
            auto_pause_when_players_connect = $false
        }
        Write-Utf8NoBom $p.Settings ($settings | ConvertTo-Json -Depth 4)
    }
    $modList = [ordered]@{ mods = @(
        [ordered]@{ name = "base"; enabled = $true },
        [ordered]@{ name = "elevated-rails"; enabled = $true },
        [ordered]@{ name = "quality"; enabled = $true },
        [ordered]@{ name = "space-age"; enabled = $true },
        [ordered]@{ name = "factorio_cursor_rl_agent"; enabled = $true },
        [ordered]@{ name = "factorio_training_lab"; enabled = $true }
    ) }
    Write-Utf8NoBom (Join-Path $p.Mods "mod-list.json") ($modList | ConvertTo-Json -Depth 4)
}

function Assert-Stopped {
    $p = Paths
    if (Test-Path -LiteralPath $p.Pid -PathType Leaf) {
        $pid = [int](Get-Content -LiteralPath $p.Pid -Raw).Trim()
        $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($null -ne $process) { throw "Windows training worker is running (PID $pid); stop it before changing files." }
        Remove-Item -LiteralPath $p.Pid -Force
    }
}

function Copy-TrainingMods {
    Assert-Stopped
    $p = Paths
    foreach ($mod in @(
        @{ Name = "factorio_training_lab"; Source = Join-Path $RepoRoot "factorio_training_lab" },
        @{ Name = "factorio_cursor_rl_agent"; Source = Join-Path $RepoRoot "factorio_mod" }
    )) {
        Require-File (Join-Path $mod.Source "control.lua")
        $target = Join-Path $p.Mods $mod.Name
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Copy-Item -LiteralPath $mod.Source -Destination $target -Recurse
    }
}

function Write-WorkerConfig {
    $workers = foreach ($slot in 1..$SlotsPerWorker) {
        [ordered]@{
            worker_id = "training-win-01-slot-{0:D2}" -f $slot
            instance_id = "factorio-training-win-01"
            host = "127.0.0.1"
            game_port = $GamePort
            rcon_port = $RconPort
            script_output = ((Paths).ScriptOutput).Replace("\", "/")
            surface_prefix = "training/"
            force_prefix = "training-"
        }
    }
    $output = if ([IO.Path]::IsPathRooted($ConfigOutput)) { $ConfigOutput } else { Join-Path $RepoRoot $ConfigOutput }
    Write-Utf8NoBom $output (([ordered]@{ workers = @($workers) } | ConvertTo-Json -Depth 5) + [Environment]::NewLine)
    Write-Host "Wrote Windows worker config for $SlotsPerWorker logical slots: $output"
}

function Start-Worker {
    if ([string]::IsNullOrWhiteSpace($RconPassword)) { throw "RCON password is required; pass -RconPassword or set FACTORIO_TRAINING_RCON_PASSWORD." }
    $p = Paths
    Require-File $p.Config; Require-File $p.Settings; Require-File $p.Save
    Require-File (Join-Path $p.Mods "factorio_training_lab\control.lua")
    Require-File (Join-Path $p.Mods "factorio_cursor_rl_agent\control.lua")
    if (Get-NetTCPConnection -State Listen -LocalPort $GamePort -ErrorAction SilentlyContinue) { throw "Training game port $GamePort is already in use." }
    if (Get-NetTCPConnection -State Listen -LocalPort $RconPort -ErrorAction SilentlyContinue) { throw "Training RCON port $RconPort is already in use." }
    Assert-Stopped
    $arguments = @(
        "--config", $p.Config, "--mod-directory", $p.Mods,
        "--start-server", $p.Save, "--server-settings", $p.Settings,
        "--port", $GamePort.ToString(), "--rcon-port", $RconPort.ToString(),
        "--rcon-password", $RconPassword, "--console-log", (Join-Path $p.Logs "factorio-console.log")
    )
    $process = Start-Process -FilePath $FactorioExe -ArgumentList $arguments `
        -RedirectStandardOutput $p.Stdout -RedirectStandardError $p.Stderr -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath $p.Pid -Value $process.Id -NoNewline
    Start-Sleep -Seconds 2
    $process.Refresh()
    if ($process.HasExited) { Remove-Item -LiteralPath $p.Pid -Force -ErrorAction SilentlyContinue; throw "Windows training worker exited; inspect $($p.Stderr)." }
    Write-Host "Started Windows training worker PID $($process.Id) game=127.0.0.1:$GamePort rcon=127.0.0.1:$RconPort"
}

function Stop-Worker {
    $p = Paths
    if (-not (Test-Path -LiteralPath $p.Pid -PathType Leaf)) { Write-Host "Windows training worker is stopped"; return }
    $pid = [int](Get-Content -LiteralPath $p.Pid -Raw).Trim()
    $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($null -eq $process) { Remove-Item -LiteralPath $p.Pid -Force; Write-Host "Windows training worker is stopped"; return }
    Stop-Process -Id $pid
    $process.WaitForExit(30000)
    if (-not $process.HasExited) { throw "Windows training worker PID $pid did not stop within 30 seconds." }
    Remove-Item -LiteralPath $p.Pid -Force
    Write-Host "Stopped Windows training worker PID $pid"
}

function Show-Status {
    $p = Paths
    if (Test-Path -LiteralPath $p.Pid -PathType Leaf) {
        $pid = [int](Get-Content -LiteralPath $p.Pid -Raw).Trim()
        $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($null -ne $process) { Write-Host "running Windows training worker PID $pid game=127.0.0.1:$GamePort rcon=127.0.0.1:$RconPort"; return }
    }
    Write-Host "Windows training worker is stopped"
}

switch ($Action) {
    "configure" { Write-WorkerConfig }
    "bootstrap" {
        Assert-StaticInputs; Ensure-Directories
        $p = Paths
        if (-not (Test-Path -LiteralPath $p.Save -PathType Leaf)) { Copy-Item -LiteralPath $SourceSave -Destination $p.Save }
        Write-ServerFiles; Copy-TrainingMods; Write-WorkerConfig
        Write-Host "Bootstrapped isolated Windows training worker at $ServerData"
    }
    "deploy" { Assert-StaticInputs; Ensure-Directories; Write-ServerFiles; Copy-TrainingMods }
    "start" { Start-Worker }
    "stop" { Stop-Worker }
    "status" { Show-Status }
}
