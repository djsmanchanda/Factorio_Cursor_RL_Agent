# Path: scripts/launch_training_worker.ps1
# Purpose: Launch the isolated training worker with its fixed local ports and data profile.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$serverData = "C:\Users\djsma\AppData\Local\Factorio-training-01"
$factorioExe = "E:\Games\Factorio\bin\x64\factorio.exe"
$configPath = Join-Path $serverData "config.ini"
$modsPath = Join-Path $serverData "mods"
$savePath = Join-Path $serverData "saves\training-01.zip"
$settingsPath = Join-Path $serverData "server-settings.json"
$logsPath = Join-Path $serverData "logs"
$stdoutPath = Join-Path $logsPath "factorio-stdout.log"
$stderrPath = Join-Path $logsPath "factorio-stderr.log"

foreach ($path in @($factorioExe, $configPath, $modsPath, $savePath, $settingsPath)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Training worker prerequisite is missing: $path"
    }
}

if (-not (Test-Path -LiteralPath $logsPath -PathType Container)) {
    New-Item -ItemType Directory -Path $logsPath -Force | Out-Null
}

if (Get-NetTCPConnection -State Listen -LocalPort 28001 -ErrorAction SilentlyContinue) {
    throw "Training worker RCON port 28001 is already in use; refusing to start another server."
}

$arguments = @(
    "--config", $configPath,
    "--mod-directory", $modsPath,
    "--start-server", $savePath,
    "--server-settings", $settingsPath,
    "--port", "35001",
    "--rcon-port", "28001",
    "--rcon-password", "password"
)

$process = Start-Process -FilePath $factorioExe -ArgumentList $arguments `
    -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath `
    -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
$process.Refresh()
if ($process.HasExited) {
    throw "Training worker exited during startup; inspect $stdoutPath and $stderrPath."
}

Write-Host "Training worker started (PID $($process.Id)) on game 35001 / RCON 28001."
