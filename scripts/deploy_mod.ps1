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

# Ship info.json plus EVERY Lua module. control.lua requires the others by
# name, so copying only control.lua makes the save fail to load with
# "module <name> not found".
Copy-Item (Join-Path $source "info.json") $target
$luaFiles = Get-ChildItem -Path $source -Filter "*.lua" -File
if ($luaFiles.Count -eq 0) { throw "No Lua files found in $source" }
foreach ($file in $luaFiles) { Copy-Item $file.FullName $target }

# Fail loudly if any module control.lua requires did not make it across.
$controlText = Get-Content (Join-Path $target "control.lua") -Raw
$missing = @()
foreach ($match in [regex]::Matches($controlText, 'require\s*\(?\s*"([^"]+)"')) {
    $moduleName = $match.Groups[1].Value -replace '^__[^_]+__/', ''
    if (-not (Test-Path (Join-Path $target "$moduleName.lua"))) { $missing += $moduleName }
}
if ($missing.Count -gt 0) { throw "Deployed mod is missing required modules: $($missing -join ', ')" }

Write-Host "Deployed factorio_cursor_rl_agent ($($luaFiles.Count) Lua modules) to $target"
