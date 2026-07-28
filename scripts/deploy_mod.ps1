# Path: scripts/deploy_mod.ps1
# Purpose: Copy factorio_mod/ into the normal and optionally dedicated Factorio mod directories.

[CmdletBinding()]
param(
    [switch]$IncludeDedicated,
    [string]$DedicatedServerData = (Join-Path $env:LOCALAPPDATA "Factorio-server")
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "factorio_mod"
$modsDir = Join-Path $env:APPDATA "Factorio\mods"

if (-not (Test-Path (Join-Path $source "info.json"))) {
    throw "Source mod not found at $source"
}
if (-not (Test-Path $modsDir)) {
    throw "Factorio mods directory not found at $modsDir - has Factorio been run at least once?"
}

$targets = @((Join-Path $modsDir "factorio_cursor_rl_agent"))
if ($IncludeDedicated) {
    $dedicatedMods = Join-Path $DedicatedServerData "mods"
    New-Item -ItemType Directory -Path $dedicatedMods -Force | Out-Null
    $targets += Join-Path $dedicatedMods "factorio_cursor_rl_agent"
}

$luaFiles = Get-ChildItem -Path $source -Filter "*.lua" -File
if ($luaFiles.Count -eq 0) { throw "No Lua files found in $source" }

foreach ($target in $targets) {
    if (Test-Path $target) {
        Remove-Item -Recurse -Force $target
    }
    New-Item -ItemType Directory -Path $target | Out-Null

    Copy-Item (Join-Path $source "info.json") $target
    foreach ($file in $luaFiles) { Copy-Item $file.FullName $target }

    # Fail loudly if any module required by control.lua was not copied.
    $controlText = Get-Content (Join-Path $target "control.lua") -Raw
    $missing = @()
    foreach ($match in [regex]::Matches($controlText, 'require\s*\(?\s*"([^"]+)"')) {
        $moduleName = $match.Groups[1].Value -replace '^__[^_]+__/', ''
        if (-not (Test-Path (Join-Path $target "$moduleName.lua"))) { $missing += $moduleName }
    }
    if ($missing.Count -gt 0) {
        throw "Deployed mod is missing required modules at ${target}: $($missing -join ', ')"
    }
    Write-Host "Deployed factorio_cursor_rl_agent ($($luaFiles.Count) Lua modules) to $target"
}
