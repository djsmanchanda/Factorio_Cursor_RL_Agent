# Path: scripts/manage_wsl_training_worker.ps1
# Purpose: Invoke the unprivileged WSL training worker without touching real-base Factorio.

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("bootstrap", "deploy", "start", "stop", "status")]
    [string]$Action,
    [string]$Distro = "Ubuntu",
    [string]$Archive = "C:\Users\djsma\Downloads\factorio-headless_linux_2.0.77.tar.xz",
    [string]$SourceSave = "C:\Users\djsma\AppData\Local\Factorio-training-01\saves\training-01.zip",
    [string]$BridgeRoot = "$env:LOCALAPPDATA\Factorio-training-wsl-01"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$workerScript = Join-Path $repoRoot "scripts\wsl\training_worker.sh"

function ConvertTo-WslPath {
    param([Parameter(Mandatory)][string]$WindowsPath)
    (& wsl.exe -d $Distro -- wslpath -a $WindowsPath.Replace("\", "/")).Trim()
}

function Assert-TrainingPortsAvailable {
    foreach ($port in @(35001, 28001)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
            throw "Training port $port is already in use; stop the existing worker before starting WSL."
        }
    }
    if (Get-NetUDPEndpoint -LocalPort 35001 -ErrorAction SilentlyContinue) {
        throw "Training game port 35001 is already in use; stop the existing worker before starting WSL."
    }
}

function Protect-SecretFile {
    param([Parameter(Mandatory)][string]$SecretPath)
    $acl = Get-Acl -LiteralPath $SecretPath
    $acl.SetAccessRuleProtection($true, $false)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, "Read", "Allow"))
    Set-Acl -LiteralPath $SecretPath -AclObject $acl
}

function Write-WorkerConfig {
    param([Parameter(Mandatory)][string]$OutputRoot)
    $output = Join-Path $repoRoot "training-workers-wsl.json"
    $payload = [ordered]@{workers = @([ordered]@{
        worker_id = "training-wsl-01"
        instance_id = "factorio-training-wsl-01"
        host = "127.0.0.1"
        game_port = 35001
        rcon_port = 28001
        script_output = (Join-Path $OutputRoot "script-output").Replace("\", "/")
        surface_prefix = "training/"
        force_prefix = "training-"
    })}
    [IO.File]::WriteAllText(
        $output, ($payload | ConvertTo-Json -Depth 4) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Write-Host "Wrote ignored local worker config: $output"
}

if (-not (Test-Path -LiteralPath $workerScript -PathType Leaf)) {
    throw "WSL training worker script is missing: $workerScript"
}

$workerScriptWsl = ConvertTo-WslPath $workerScript
switch ($Action) {
    "bootstrap" {
        foreach ($path in @($Archive, $SourceSave)) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                throw "Bootstrap input is missing: $path"
            }
        }
        New-Item -ItemType Directory -Path $BridgeRoot -Force | Out-Null
        & wsl.exe -d $Distro -- bash $workerScriptWsl bootstrap `
            (ConvertTo-WslPath $Archive) (ConvertTo-WslPath $SourceSave) `
            (ConvertTo-WslPath $repoRoot) (ConvertTo-WslPath $BridgeRoot)
        if ($LASTEXITCODE -ne 0) { throw "WSL worker bootstrap failed." }
        Protect-SecretFile (Join-Path $BridgeRoot "rcon-password")
        Write-WorkerConfig $BridgeRoot
    }
    "deploy" {
        & wsl.exe -d $Distro -- bash $workerScriptWsl deploy (ConvertTo-WslPath $repoRoot)
        if ($LASTEXITCODE -ne 0) { throw "WSL worker deployment failed." }
    }
    "start" {
        Assert-TrainingPortsAvailable
        & wsl.exe -d $Distro -- bash $workerScriptWsl start
        if ($LASTEXITCODE -ne 0) { throw "WSL worker action failed: start" }
    }
    "stop" {
        & wsl.exe -d $Distro -- bash $workerScriptWsl stop
        if ($LASTEXITCODE -ne 0) { throw "WSL worker action failed: stop" }
    }
    "status" {
        & wsl.exe -d $Distro -- bash $workerScriptWsl status
        if ($LASTEXITCODE -ne 0) { throw "WSL worker action failed: status" }
    }
}