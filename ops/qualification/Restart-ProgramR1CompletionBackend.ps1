param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,
    [Parameter(Mandatory = $true)]
    [string]$TemporaryRoot,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedHead,
    [int]$BackendPort = 58081,
    [string]$PythonExecutable = (Get-Command python -ErrorAction Stop).Source
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomAtomic {
    param([string]$Path, [string]$Value)
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary, $Value, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$worktreeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$resolvedEvidenceRoot = (Resolve-Path -LiteralPath $EvidenceRoot).Path
$resolvedTemporaryRoot = (Resolve-Path -LiteralPath $TemporaryRoot).Path
if (-not $resolvedEvidenceRoot.StartsWith("C:\AgroSat_backups\PROGRAM_R1_COMPLETION_RUN\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unexpected completion evidence root."
}
if (-not $resolvedTemporaryRoot.StartsWith("C:\tmp\agrosat_r1_completion_", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unexpected isolated temporary root."
}
$actualHead = (& git -C $worktreeRoot rev-parse HEAD).Trim()
if ($actualHead -ne $ExpectedHead) {
    throw "Unexpected worktree HEAD."
}

$runtimeStatePath = Join-Path $resolvedEvidenceRoot "scratch\ISOLATED_RUNTIME_STATE.json"
$runtimeState = Get-Content -LiteralPath $runtimeStatePath -Raw | ConvertFrom-Json
$oldPid = [int]$runtimeState.pids.backend
$listener = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue
$backendProcess = $null
$health = $null
$reuseRunningProcess = $false
if ($listener) {
    if (@($listener).Count -ne 1) {
        throw "Backend port has an unexpected number of listeners."
    }
    $listenerPid = [int]$listener.OwningProcess
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid"
    if (-not $process -or $process.CommandLine -notmatch 'uvicorn' -or $process.CommandLine -notmatch "--port\s+$BackendPort") {
        throw "Recorded backend process identity could not be verified."
    }
    if ($listenerPid -ne $oldPid) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/health/ready" -TimeoutSec 2
        } catch {
            throw "Unrecorded backend listener is not a ready qualification process."
        }
        if ($health.release_revision -ne $ExpectedHead) {
            throw "Unrecorded backend listener has an unexpected release revision."
        }
        $backendProcess = Get-Process -Id $listenerPid
        $reuseRunningProcess = $true
    } else {
        Stop-Process -Id $oldPid
        for ($attempt = 0; $attempt -lt 40; $attempt += 1) {
            if (-not (Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 100
        }
    }
}
if (-not $reuseRunningProcess -and (Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue)) {
    throw "Backend port did not become available."
}

$runtimeEnvironment = Join-Path $resolvedTemporaryRoot "runtime.env"
if (-not (Test-Path -LiteralPath $runtimeEnvironment -PathType Leaf)) {
    throw "Protected runtime environment is unavailable."
}
$runtimeText = [IO.File]::ReadAllText($runtimeEnvironment)
if ($runtimeText -match '(?m)^RELEASE_REVISION=') {
    $runtimeText = [regex]::Replace($runtimeText, '(?m)^RELEASE_REVISION=.*$', "RELEASE_REVISION=$ExpectedHead")
} else {
    $runtimeText = $runtimeText.TrimEnd() + [Environment]::NewLine + "RELEASE_REVISION=$ExpectedHead" + [Environment]::NewLine
}
if ($runtimeText -match '(?m)^WIALON_ENABLED=') {
    $runtimeText = [regex]::Replace($runtimeText, '(?m)^WIALON_ENABLED=.*$', 'WIALON_ENABLED=false')
} else {
    $runtimeText = $runtimeText.TrimEnd() + [Environment]::NewLine + 'WIALON_ENABLED=false' + [Environment]::NewLine
}
Write-Utf8NoBomAtomic -Path $runtimeEnvironment -Value $runtimeText

if (-not $reuseRunningProcess) {
    $env:AGROSAT_RUNTIME_ENV_FILE = $runtimeEnvironment
    try {
        $backendProcess = Start-Process `
            -FilePath $PythonExecutable `
            -ArgumentList @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "$BackendPort", "--no-access-log") `
            -WorkingDirectory (Join-Path $worktreeRoot "backend") `
            -RedirectStandardOutput (Join-Path $resolvedTemporaryRoot "backend-restart-standard.out") `
            -RedirectStandardError (Join-Path $resolvedTemporaryRoot "backend-restart-standard.err") `
            -WindowStyle Hidden `
            -PassThru
    } finally {
        Remove-Item Env:\AGROSAT_RUNTIME_ENV_FILE -ErrorAction SilentlyContinue
    }

    for ($attempt = 0; $attempt -lt 80; $attempt += 1) {
        Start-Sleep -Milliseconds 250
        if ($backendProcess.HasExited) { break }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$BackendPort/health/ready" -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $health = $response.Content | ConvertFrom-Json
                break
            }
        } catch {
            # Expected during isolated process startup.
        }
    }
}
if ($null -eq $health) {
    throw "Restarted isolated backend did not become ready."
}

$runtimeState.pids.backend = $backendProcess.Id
$runtimeState | Add-Member -NotePropertyName currentHead -NotePropertyValue $ExpectedHead -Force
$runtimeState | Add-Member -NotePropertyName backendRestartedAt -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString("o")) -Force
Write-Utf8NoBomAtomic -Path $runtimeStatePath -Value ($runtimeState | ConvertTo-Json -Depth 15)

$evidence = [ordered]@{
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
    address = "127.0.0.1"
    port = $BackendPort
    pid = $backendProcess.Id
    head = $ExpectedHead
    runtimeConfigClass = "protected_ephemeral_explicit"
    migrationHead = $health.components.database.migration_revision
    releaseRevision = $health.release_revision
    wialonEnabled = $false
    credentialsPrinted = $false
    productionContacted = $false
    productionWrites = 0
}
Write-Utf8NoBomAtomic `
    -Path (Join-Path $resolvedEvidenceRoot "03_BROWSER_MOBILE_OFFLINE\BACKEND_RUNTIME_READY_AFTER_REPAIR.json") `
    -Value ($evidence | ConvertTo-Json -Depth 10)

Write-Output "BACKEND_RESTARTED_HEAD=$ExpectedHead; PID=$($backendProcess.Id); WIALON_ENABLED=false; PRODUCTION_WRITES=0"
