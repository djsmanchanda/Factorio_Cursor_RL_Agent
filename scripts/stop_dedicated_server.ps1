# Path: scripts/stop_dedicated_server.ps1
# Purpose: Stop the exact dedicated Factorio process and its elevated launcher shell.

[CmdletBinding()]
param(
    [string]$ServerData = "C:\Users\djsma\AppData\Local\Factorio-server",
    [ValidateRange(1, 65535)]
    [int]$RconPort = 27017
)

$ErrorActionPreference = "Stop"
$expectedConfig = Join-Path $ServerData "config.ini"
$connection = Get-NetTCPConnection -State Listen -LocalPort $RconPort `
    -ErrorAction SilentlyContinue | Select-Object -First 1

if ($null -ne $connection) {
    $matches = @(
        Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)"
    )
}
else {
    $matches = @(
        Get-CimInstance Win32_Process -Filter "Name='factorio.exe'" |
            Where-Object { $_.CommandLine -like "*$expectedConfig*" }
    )
}

if ($matches.Count -gt 1) {
    throw "Multiple Factorio processes use the configured dedicated server data."
}
$processInfo = $matches | Select-Object -First 1
if (
    $null -ne $processInfo -and (
        $processInfo.Name -ne "factorio.exe" -or
        $processInfo.CommandLine -notlike "*$expectedConfig*"
    )
) {
    throw "Port $RconPort is not owned by the configured dedicated Factorio server."
}

if ($null -ne $processInfo) {
    Stop-Process -Id $processInfo.ProcessId -Force
    Wait-Process -Id $processInfo.ProcessId -ErrorAction SilentlyContinue
    Write-Host "Stopped dedicated Factorio server PID $($processInfo.ProcessId)."
}
else {
    Write-Host "Dedicated Factorio process is already stopped."
}

$launchers = @(
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object {
            $_.CommandLine -like "*launch_dedicated_server.ps1*" -and
            $_.CommandLine -like "*$ServerData*"
        }
)
foreach ($launcher in $launchers) {
    Stop-Process -Id $launcher.ProcessId -Force
    Write-Host "Stopped dedicated server launcher shell PID $($launcher.ProcessId)."
}