# Path: scripts/deploy_mod.ps1
# Purpose: Copy factorio_mod/ into the Factorio mods directory so the game loads it on next launch.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "factorio_mod"
$modsDir = Join-Path $env:APPDATA "Factorio\mods"
$target = Join-Path $modsDir "factorio_cursor_rl_agent"

if (-not (Test-Path (Join-Path $source "info.json"))) {
    throw "Source mod not found at $source"
}
if (-not (Test-Path $modsDir)) {
    throw "Factorio mods directory not found at $modsDir - has Factorio been run at least once?"
}

if (Test-Path $target) {
    Remove-Item -Recurse -Force $target
}
New-Item -ItemType Directory -Path $target | Out-Null

# Only ship what the game needs; README is harmless but keeps the deploy honest.
Copy-Item (Join-Path $source "info.json") $target
Copy-Item (Join-Path $source "control.lua") $target

Write-Host "Deployed factorio_cursor_rl_agent to $target"
