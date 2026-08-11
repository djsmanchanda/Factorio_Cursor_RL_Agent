# Path: scripts/benchmark_wsl_training_slots.ps1
# Purpose: Empirically scale one WSL Factorio runtime's concurrent RL slots without overrunning the laptop.

[CmdletBinding()]
param(
    [ValidateRange(1, 20)]
    [int]$MaximumSlots = 20,
    [ValidateRange(1, 20)]
    [int]$StartSlots = 4,
    [ValidateRange(1, 8)]
    [int]$Step = 4,
    [ValidateRange(1, 100)]
    [int]$AttemptsPerStage = 1,
    [ValidateRange(1, 100)]
    [int]$MaximumCpuPercent = 90,
    [ValidateRange(512, 65536)]
    [int]$MinimumAvailableMemoryMB = 4096,
    [ValidateRange(1.1, 10.0)]
    [double]$MaximumSlowdown = 2.0,
    [ValidateRange(0.0, 1.0)]
    [double]$MinimumCompletionRate = 0.75,
    [string]$Distro = "Ubuntu",
    [string]$CapacityDirectory
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $CapacityDirectory) {
    $CapacityDirectory = Join-Path $repoRoot ("data\training-capacity\slots-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}
New-Item -ItemType Directory -Path $CapacityDirectory -Force | Out-Null
$manager = Join-Path $PSScriptRoot "manage_wsl_training_worker.ps1"
$runner = Join-Path $PSScriptRoot "run_wsl_training_batch.ps1"
$history = @()
$baselineSeconds = $null

function Get-HostSample {
    $values = Get-Counter '\Processor Information(_Total)\% Processor Time','\Memory\Available MBytes' |
        Select-Object -ExpandProperty CounterSamples
    [ordered]@{
        cpu_percent = [double](($values | Where-Object { $_.Path -match 'Processor Information' }).CookedValue)
        available_memory_mb = [double](($values | Where-Object { $_.Path -match 'Available MBytes' }).CookedValue)
    }
}

for ($slots = $StartSlots; $slots -le $MaximumSlots; $slots += $Step) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $manager -Action configure -WorkerCount 1 -SlotsPerWorker $slots
    if ($LASTEXITCODE -ne 0) { throw "could not configure $slots training slots" }

    $samples = [System.Collections.Generic.List[object]]::new()
    $stopPath = Join-Path $CapacityDirectory ("stop-" + [guid]::NewGuid())
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $job = Start-Job -ScriptBlock {
        param($limit, $sampleInterval)
        while (-not (Test-Path -LiteralPath $limit)) {
            $values = Get-Counter '\Processor Information(_Total)\% Processor Time','\Memory\Available MBytes' |
                Select-Object -ExpandProperty CounterSamples
            [PSCustomObject]@{
                cpu_percent = [double](($values | Where-Object { $_.Path -match 'Processor Information' }).CookedValue)
                available_memory_mb = [double](($values | Where-Object { $_.Path -match 'Available MBytes' }).CookedValue)
            }
            Start-Sleep -Seconds $sampleInterval
        }
    } -ArgumentList $stopPath, 2
    try {
        $output = & powershell -NoProfile -ExecutionPolicy Bypass -File $runner -Distro $Distro --count $slots --attempts-per-scenario $AttemptsPerStage --database (Join-Path $CapacityDirectory "experience.db") --checkpoint (Join-Path $CapacityDirectory "policy.json") --live-directory (Join-Path $CapacityDirectory "live") 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        New-Item -ItemType File -Path $stopPath -Force | Out-Null
        Start-Sleep -Milliseconds 100
        Stop-Job -Job $job -ErrorAction SilentlyContinue
        $samples.AddRange(@(Receive-Job -Job $job -ErrorAction SilentlyContinue))
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stopPath -Force -ErrorAction SilentlyContinue
    }
    $watch.Stop()
    try {
        $result = ($output | Out-String | ConvertFrom-Json)
    }
    catch {
        throw "training probe returned no structured result at $slots slots: $output"
    }
    if ($exitCode -ne 0 -and [int]$result.attempts -ne $slots) {
        throw "training probe controller failed at $slots slots: $output"
    }
    if ($samples.Count -eq 0) { $samples.Add([pscustomobject](Get-HostSample)) }
    $averageCpu = ($samples | Measure-Object -Property cpu_percent -Average).Average
    $minimumMemory = ($samples | Measure-Object -Property available_memory_mb -Minimum).Minimum
    $record = [ordered]@{
        slots = $slots
        elapsed_seconds = [math]::Round($watch.Elapsed.TotalSeconds, 3)
        completed = [int]$result.completed
        failed = [int]$result.failed
        completion_rate = [math]::Round(([double]$result.completed / $slots), 4)
        average_cpu_percent = [math]::Round($averageCpu, 2)
        minimum_available_memory_mb = [math]::Round($minimumMemory, 0)
    }
    $history += [pscustomobject]$record
    $healthy = $record.completion_rate -ge $MinimumCompletionRate -and
        $record.average_cpu_percent -le $MaximumCpuPercent -and
        $record.minimum_available_memory_mb -ge $MinimumAvailableMemoryMB
    if ($null -eq $baselineSeconds) { $baselineSeconds = $record.elapsed_seconds }
    elseif ($record.elapsed_seconds -gt ($baselineSeconds * $MaximumSlowdown)) { $healthy = $false }
    $record['healthy'] = [bool]$healthy
    $history += [pscustomobject]$record
    $record | ConvertTo-Json -Compress | Write-Output
    if (-not $healthy) { break }
}

$history | ConvertTo-Json -Depth 3 | Tee-Object -FilePath (Join-Path $CapacityDirectory "capacity-summary.json")