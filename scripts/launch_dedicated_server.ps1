# Path: scripts/launch_dedicated_server.ps1
# Purpose: Initialize and launch an isolated local Factorio server without locking the normal GUI profile.

<#
.SYNOPSIS
Creates, then launches, an isolated local Factorio dedicated server.

.DESCRIPTION
The first run copies the source save and deployed mod into a dedicated server data
directory. Later runs preserve those copies. The normal Factorio GUI profile and its
Start-menu shortcut are never modified.

Defaults:
  Server data: C:\Users\djsma\AppData\Local\Factorio-server
  Source save: C:\Users\djsma\AppData\Roaming\Factorio\saves\mod_playground.zip
  Source mod:  C:\Users\djsma\AppData\Roaming\Factorio\mods\factorio_cursor_rl_agent
  Factorio:    E:\Games\Factorio\bin\x64\factorio.exe
  Read data:   E:\Games\Factorio\data
  Game/RCON:   34199 / 27017
  RCON pass:   planner_test

.EXAMPLE
.\scripts\launch_dedicated_server.ps1

.EXAMPLE
Get-Help .\scripts\launch_dedicated_server.ps1 -Detailed

.NOTES
Run this only when starting the server is authorized. The script does not elevate
itself. Stop the dedicated server normally before launching it again.
#>

[CmdletBinding()]
param(
    [string]$ServerData = "C:\Users\djsma\AppData\Local\Factorio-server",
    [string]$SourceSave = "C:\Users\djsma\AppData\Roaming\Factorio\saves\mod_playground.zip",
    [string]$SourceMod = "C:\Users\djsma\AppData\Roaming\Factorio\mods\factorio_cursor_rl_agent",
    [string]$FactorioExe = "E:\Games\Factorio\bin\x64\factorio.exe",
    [string]$ReadData = "E:\Games\Factorio\data",
    [ValidateRange(1, 65535)]
    [int]$GamePort = 34199,
    [ValidateRange(1, 65535)]
    [int]$RconPort = 27017,
    [string]$RconPassword = "planner_test"
)

$ErrorActionPreference = "Stop"

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory)]
        [string]$LiteralPath,
        [Parameter(Mandatory)]
        [string]$Content
    )

    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($LiteralPath, $Content, $encoding)
}

function Test-ExclusiveFileAccess {
    param(
        [Parameter(Mandatory)]
        [string]$LiteralPath
    )

    if (-not (Test-Path -LiteralPath $LiteralPath -PathType Leaf)) {
        return $true
    }

    try {
        $stream = [System.IO.File]::Open(
            $LiteralPath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $stream.Dispose()
        return $true
    }
    catch {
        return $false
    }
}

function Assert-ConfigValue {
    param(
        [Parameter(Mandatory)]
        [string]$ConfigText,
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$Expected
    )

    $pattern = "(?m)^\s*" + [regex]::Escape($Name) + "\s*=\s*" +
        [regex]::Escape($Expected) + "\s*$"
    if ($ConfigText -notmatch $pattern) {
        throw "Existing config.ini does not set '$Name' to '$Expected'. Refusing to overwrite it."
    }
}

if (-not (Test-Path -LiteralPath $FactorioExe -PathType Leaf)) {
    throw "Factorio executable not found: $FactorioExe"
}
if (-not (Test-Path -LiteralPath $ReadData -PathType Container)) {
    throw "Factorio read-data directory not found: $ReadData"
}
if (-not (Test-Path -LiteralPath $SourceSave -PathType Leaf)) {
    throw "Source save not found: $SourceSave"
}
if (-not (Test-Path -LiteralPath $SourceMod -PathType Container)) {
    throw "Source deployed mod not found: $SourceMod"
}
foreach ($requiredModFile in @("info.json", "control.lua")) {
    $requiredPath = Join-Path $SourceMod $requiredModFile
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Source deployed mod is incomplete; missing: $requiredPath"
    }
}

$lockPath = Join-Path $ServerData ".lock"
if (-not (Test-ExclusiveFileAccess -LiteralPath $lockPath)) {
    throw "Dedicated server lock is held or inaccessible: $lockPath"
}
if (Test-Path -LiteralPath $lockPath -PathType Leaf) {
    Remove-Item -LiteralPath $lockPath -Force
    Write-Host "Removed unheld stale dedicated-server lock: $lockPath"
}

$savesDir = Join-Path $ServerData "saves"
$modsDir = Join-Path $ServerData "mods"
$logsDir = Join-Path $ServerData "logs"
$scriptOutputDir = Join-Path $ServerData "script-output"
foreach ($directory in @($ServerData, $savesDir, $modsDir, $logsDir, $scriptOutputDir)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$targetSave = Join-Path $savesDir "mod_playground.zip"
$targetMod = Join-Path $modsDir "factorio_cursor_rl_agent"
$configPath = Join-Path $ServerData "config.ini"
$settingsPath = Join-Path $ServerData "server-settings.json"

if (-not (Test-Path -LiteralPath $targetSave -PathType Leaf)) {
    Copy-Item -LiteralPath $SourceSave -Destination $targetSave
    Write-Host "Copied initial server save to $targetSave"
}

if (-not (Test-Path -LiteralPath $targetMod -PathType Container)) {
    Copy-Item -LiteralPath $SourceMod -Destination $targetMod -Recurse
    Write-Host "Copied initial server mod to $targetMod"
}
foreach ($requiredModFile in @("info.json", "control.lua")) {
    $requiredPath = Join-Path $targetMod $requiredModFile
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Dedicated server mod is incomplete; missing: $requiredPath"
    }
}

$configContent = @"
[path]
read-data=$ReadData
write-data=$ServerData
"@
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    $existingConfig = Get-Content -LiteralPath $configPath -Raw
    Assert-ConfigValue -ConfigText $existingConfig -Name "read-data" -Expected $ReadData
    Assert-ConfigValue -ConfigText $existingConfig -Name "write-data" -Expected $ServerData
}
else {
    Write-Utf8NoBom -LiteralPath $configPath -Content $configContent
}

if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
    $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    if (
        $settings.auto_pause -ne $false -or
        $settings.auto_pause_when_players_connect -ne $false -or
        $settings.require_user_verification -ne $false
    ) {
        throw "Existing server-settings.json does not contain the required local-server settings. Refusing to overwrite it."
    }
}
else {
    $settings = [ordered]@{
        name = "Factorio RL Local Server"
        description = "Private local Factorio RL development server"
        visibility = [ordered]@{
            public = $false
            lan = $true
        }
        game_password = ""
        require_user_verification = $false
        auto_pause = $false
        auto_pause_when_players_connect = $false
    }
    Write-Utf8NoBom -LiteralPath $settingsPath -Content ($settings | ConvertTo-Json -Depth 4)
}

$stdoutPath = Join-Path $logsDir "factorio-stdout.log"
$stderrPath = Join-Path $logsDir "factorio-stderr.log"
$launchArguments = @(
    "--config", $configPath,
    "--mod-directory", $modsDir,
    "--start-server", $targetSave,
    "--server-settings", $settingsPath,
    "--port", $GamePort.ToString(),
    "--rcon-port", $RconPort.ToString(),
    "--rcon-password", $RconPassword
)

$process = Start-Process `
    -FilePath $FactorioExe `
    -ArgumentList $launchArguments `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2
$process.Refresh()
if ($process.HasExited) {
    throw "Factorio server exited during startup. Inspect $stdoutPath and $stderrPath"
}

Write-Host "Dedicated Factorio server started (PID $($process.Id))."
Write-Host "Game: 127.0.0.1:$GamePort  RCON: 127.0.0.1:$RconPort"
Write-Host "Server data: $ServerData"
Write-Host "Script output: $scriptOutputDir"
Write-Host "Server stdout: $stdoutPath"
Write-Host "Server stderr: $stderrPath"
