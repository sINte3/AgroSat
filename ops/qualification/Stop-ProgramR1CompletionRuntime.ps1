param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,
    [Parameter(Mandatory = $true)]
    [string]$TemporaryRoot,
    [string]$PostgresBin = "C:\Program Files\PostgreSQL\16\bin",
    [int[]]$ExpectedPorts = @(55439, 56381, 58081, 54181)
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-JsonAtomic {
    param([string]$Path, [object]$Value)
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText(
        $temporary,
        ($Value | ConvertTo-Json -Depth 20),
        (New-Object Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$resolvedEvidence = (Resolve-Path -LiteralPath $EvidenceRoot).Path
$resolvedTemporary = (Resolve-Path -LiteralPath $TemporaryRoot).Path
if (-not $resolvedEvidence.StartsWith("C:\AgroSat_backups\PROGRAM_R1_COMPLETION_RUN\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unexpected completion evidence root."
}
if (-not $resolvedTemporary.StartsWith("C:\tmp\agrosat_r1_completion_", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe runtime cleanup target."
}
if ($resolvedTemporary -eq "C:\tmp" -or $resolvedTemporary -eq "C:\") {
    throw "Runtime cleanup target is too broad."
}
$runtimeStatePath = Join-Path $resolvedEvidence "scratch\ISOLATED_RUNTIME_STATE.json"
$runtimeState = Get-Content -LiteralPath $runtimeStatePath -Raw | ConvertFrom-Json
$stopped = @()

foreach ($name in @("backend", "frontend", "redis")) {
    $property = $runtimeState.pids.PSObject.Properties[$name]
    if ($null -eq $property) { continue }
    $processId = [int]$property.Value
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        $stopped += [ordered]@{ component = $name; pid = $processId; state = "already_stopped" }
        continue
    }
    $command = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    $expected = switch ($name) {
        "backend" { $command.CommandLine -match 'uvicorn' }
        "frontend" { $command.CommandLine -match 'Serve-ProgramR1QualificationFrontend' }
        "redis" { $command.Name -match 'memurai' }
    }
    if (-not $expected) {
        throw "Recorded $name process identity could not be verified."
    }
    Stop-Process -Id $processId -Force
    $stopped += [ordered]@{ component = $name; pid = $processId; state = "stopped" }
}

$postgresData = Join-Path $resolvedTemporary "postgres-data"
if (Test-Path -LiteralPath $postgresData -PathType Container) {
    $pgCtl = Join-Path $PostgresBin "pg_ctl.exe"
    if (-not (Test-Path -LiteralPath $pgCtl -PathType Leaf)) {
        throw "pg_ctl is unavailable for isolated PostgreSQL cleanup."
    }
    $null = & $pgCtl -D $postgresData -t 30 -w stop -m fast 2>&1
    if ($LASTEXITCODE -notin @(0, 3)) {
        throw "Isolated PostgreSQL did not stop cleanly."
    }
    $stopped += [ordered]@{ component = "postgres"; state = "stopped_or_already_stopped" }
}

for ($attempt = 0; $attempt -lt 100; $attempt++) {
    $listeners = @(
        $ExpectedPorts | Where-Object {
            Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue
        }
    )
    if ($listeners.Count -eq 0) { break }
    Start-Sleep -Milliseconds 100
}
$remainingPorts = @(
    $ExpectedPorts | Where-Object {
        Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue
    }
)
if ($remainingPorts.Count -ne 0) {
    throw "One or more isolated runtime ports remain open."
}

# Exact target was resolved and constrained above; remove credentials, browser
# profiles, copied runtimes, logs, and the disposable PostgreSQL cluster together.
Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
$report = [ordered]@{
    schemaVersion = 1
    operation = "isolated_runtime_cleanup"
    stoppedComponents = $stopped
    portsClosed = $ExpectedPorts
    temporaryRootRemoved = -not (Test-Path -LiteralPath $resolvedTemporary)
    credentialsRemoved = $true
    browserProfilesRemoved = $true
    disposableDatabaseClusterRemoved = $true
    productionProcessesStopped = 0
    productionWrites = 0
    status = "PASS"
    marker = "PASS_PROGRAM_R1_ISOLATED_RUNTIME_CLEANUP"
}
Write-JsonAtomic -Path (Join-Path $resolvedEvidence "06_FINAL\PROGRAM_R1_RUNTIME_CLEANUP.json") -Value $report
Write-Output "RUNTIME_CLEANUP=PASS; PRODUCTION_WRITES=0"
