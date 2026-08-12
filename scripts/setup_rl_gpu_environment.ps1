# Path: scripts/setup_rl_gpu_environment.ps1
# Purpose: Create and inspect an isolated CUDA PyTorch environment for offline RL learning.

[CmdletBinding()]
param(
    [ValidateSet("install", "status")]
    [string]$Action = "status",
    [string]$EnvironmentPath
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($EnvironmentPath)) {
    $EnvironmentPath = Join-Path (Split-Path -Parent $PSCommandPath) "..\data\rl-gpu-venv"
}
$environmentPython = Join-Path $EnvironmentPath "Scripts\python.exe"

if ($Action -eq "install") {
    $python312 = "C:\Users\djsma\AppData\Local\Programs\Python\Python312\python.exe"
    if (-not (Test-Path -LiteralPath $python312)) {
        throw "Python 3.12 is required at $python312. Install it before creating the isolated GPU environment."
    }
    if (-not (Test-Path -LiteralPath $environmentPython)) {
        & $python312 -m venv $EnvironmentPath
    }
    & $environmentPython -m pip install --upgrade pip
    & $environmentPython -m pip install --upgrade "torch==2.9.1" --index-url https://download.pytorch.org/whl/cu128
    & $environmentPython -m pip install --upgrade numpy
    & $environmentPython -m pip install --upgrade jsonschema
}

if (-not (Test-Path -LiteralPath $environmentPython)) {
    throw "RL GPU environment is not installed. Run: powershell -File scripts\setup_rl_gpu_environment.ps1 -Action install"
}

$status = & $environmentPython -c 'import json, torch; print(json.dumps(dict(torch=torch.__version__, torch_cuda=torch.version.cuda, cuda_available=torch.cuda.is_available(), device_name=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)))'
if ($LASTEXITCODE -ne 0) { throw "CUDA runtime probe failed" }
$status
