# Path: scripts/launch_dashboard.ps1
# Purpose: Start the local Factorio operations dashboard and open it in the default browser.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboard = Join-Path $repoRoot "tools\dashboard_server.py"

Start-Process powershell.exe `
    -WorkingDirectory $repoRoot `
    -ArgumentList @("-NoExit", "-Command", "python -u `"$dashboard`"")

Start-Sleep -Seconds 1
Start-Process "http://127.0.0.1:9137/"
