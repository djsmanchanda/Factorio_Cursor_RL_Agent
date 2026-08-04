# Path: scripts/launch_dashboard.ps1
# Purpose: Start the local Factorio operations dashboard without a persistent console window.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboard = Join-Path $repoRoot "tools\dashboard_server.py"
$dashboardUrl = "http://127.0.0.1:9137/"

try {
    Invoke-WebRequest -UseBasicParsing -Uri $dashboardUrl -TimeoutSec 1 | Out-Null
    $online = $true
} catch {
    $online = $false
}

if (-not $online) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw)) {
        $pythonw = $python
    }
    Start-Process -FilePath $pythonw `
        -WindowStyle Hidden `
        -WorkingDirectory $repoRoot `
        -ArgumentList @("-u", "`"$dashboard`"")

    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 200
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $dashboardUrl -TimeoutSec 1 | Out-Null
            $online = $true
        } catch {
            $online = $false
        }
    } until ($online -or (Get-Date) -ge $deadline)
    if (-not $online) {
        throw "Dashboard did not start on $dashboardUrl"
    }
}

Start-Process $dashboardUrl
