[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,

    [Parameter(Mandatory = $true)]
    [string]$TemporaryRoot,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedHead,

    [int]$BackendPort = 58081,
    [int]$FrontendPort = 54181,
    [string]$PythonExecutable = (Get-Command python -ErrorAction Stop).Source,
    [string]$NodeExecutable = (Get-Command node -ErrorAction Stop).Source
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AllowedTemporaryPrefix = "C:\tmp\agrosat_r1_completion_"
$WorktreeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$BackendRoot = Join-Path $WorktreeRoot "backend"
$FrontendDist = Join-Path $WorktreeRoot "frontend\dist"
$FrontendServer = Join-Path $PSScriptRoot "Serve-ProgramR1QualificationFrontend.mjs"
$RuntimeEnvironment = Join-Path $TemporaryRoot "runtime.env"

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Value
    )

    [System.IO.File]::WriteAllText(
        $Path,
        $Value,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Assert-PortAvailable {
    param([int]$Port)

    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Qualification port $Port is already in use."
    }
}

if (-not $TemporaryRoot.StartsWith($AllowedTemporaryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "TemporaryRoot must remain under the dedicated qualification prefix."
}
$resolvedTemporaryRoot = (Resolve-Path -LiteralPath $TemporaryRoot).Path
$resolvedEvidenceRoot = (Resolve-Path -LiteralPath $EvidenceRoot).Path
if (-not (Test-Path -LiteralPath $RuntimeEnvironment -PathType Leaf)) {
    throw "Protected qualification runtime configuration is missing."
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendDist "index.html") -PathType Leaf)) {
    throw "The production frontend bundle is missing."
}
if (-not (Test-Path -LiteralPath $FrontendServer -PathType Leaf)) {
    throw "The qualification frontend server is missing."
}
if ((git -C $WorktreeRoot rev-parse HEAD).Trim() -ne $ExpectedHead) {
    throw "Unexpected application HEAD."
}

Assert-PortAvailable -Port $BackendPort
Assert-PortAvailable -Port $FrontendPort

$stagePath = Join-Path $resolvedEvidenceRoot "scratch\APPLICATION_CURRENT_STAGE.txt"
Write-Utf8NoBom -Path $stagePath -Value "starting_backend"

$env:AGROSAT_RUNTIME_ENV_FILE = $RuntimeEnvironment
try {
    $backendProcess = Start-Process `
        -FilePath $PythonExecutable `
        -ArgumentList @(
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "$BackendPort",
            "--no-access-log"
        ) `
        -WorkingDirectory $BackendRoot `
        -RedirectStandardOutput (Join-Path $resolvedTemporaryRoot "backend-standard.out") `
        -RedirectStandardError (Join-Path $resolvedTemporaryRoot "backend-standard.err") `
        -WindowStyle Hidden `
        -PassThru
}
finally {
    Remove-Item Env:\AGROSAT_RUNTIME_ENV_FILE -ErrorAction SilentlyContinue
}

$backendReady = $false
$backendHealth = $null
for ($attempt = 0; $attempt -lt 80; $attempt += 1) {
    Start-Sleep -Milliseconds 250
    if ($backendProcess.HasExited) {
        break
    }
    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$BackendPort/health/ready" `
            -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $backendHealth = $response.Content | ConvertFrom-Json
            $backendReady = $true
            break
        }
    }
    catch {
        # Readiness polling is expected while the exact isolated app starts.
    }
}
if (-not $backendReady) {
    throw "Isolated FastAPI backend did not become ready."
}
Write-Utf8NoBom -Path $stagePath -Value "backend_ready"

$backendEvidence = [ordered]@{
    checkedAt = (Get-Date).ToString("o")
    address = "127.0.0.1"
    port = $BackendPort
    pid = $backendProcess.Id
    head = $ExpectedHead
    runtimeConfigClass = "protected_ephemeral_explicit"
    health = $backendHealth
    productionContacted = $false
    productionWrites = 0
}
Write-Utf8NoBom `
    -Path (Join-Path $resolvedEvidenceRoot "03_BROWSER_MOBILE_OFFLINE\BACKEND_RUNTIME_READY.json") `
    -Value ($backendEvidence | ConvertTo-Json -Depth 12)

$env:QUALIFICATION_FRONTEND_PORT = "$FrontendPort"
$env:QUALIFICATION_BACKEND_PORT = "$BackendPort"
$env:QUALIFICATION_DIST_ROOT = $FrontendDist
try {
    $frontendProcess = Start-Process `
        -FilePath $NodeExecutable `
        -ArgumentList @($FrontendServer) `
        -WorkingDirectory $WorktreeRoot `
        -RedirectStandardOutput (Join-Path $resolvedTemporaryRoot "frontend-standard.out") `
        -RedirectStandardError (Join-Path $resolvedTemporaryRoot "frontend-standard.err") `
        -WindowStyle Hidden `
        -PassThru
}
finally {
    Remove-Item Env:\QUALIFICATION_FRONTEND_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:\QUALIFICATION_BACKEND_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:\QUALIFICATION_DIST_ROOT -ErrorAction SilentlyContinue
}

$frontendReady = $false
for ($attempt = 0; $attempt -lt 80; $attempt += 1) {
    Start-Sleep -Milliseconds 250
    if ($frontendProcess.HasExited) {
        break
    }
    try {
        $login = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$FrontendPort/login" `
            -TimeoutSec 2
        $serviceWorker = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$FrontendPort/sw.js" `
            -TimeoutSec 2
        $proxyHealth = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$FrontendPort/health/ready" `
            -TimeoutSec 2
        if (
            $login.StatusCode -eq 200 `
            -and $serviceWorker.StatusCode -eq 200 `
            -and $proxyHealth.StatusCode -eq 200
        ) {
            $frontendReady = $true
            break
        }
    }
    catch {
        # Readiness polling is expected while the production server starts.
    }
}
if (-not $frontendReady) {
    throw "Production-like frontend server did not become ready."
}
Write-Utf8NoBom -Path $stagePath -Value "application_ready"

$frontendEvidence = [ordered]@{
    checkedAt = (Get-Date).ToString("o")
    address = "127.0.0.1"
    port = $FrontendPort
    pid = $frontendProcess.Id
    head = $ExpectedHead
    bundleClass = "production_vite_build"
    sameOriginApiProxy = $true
    serviceWorkerRoute = "available"
    productionContacted = $false
    productionWrites = 0
}
Write-Utf8NoBom `
    -Path (Join-Path $resolvedEvidenceRoot "03_BROWSER_MOBILE_OFFLINE\FRONTEND_RUNTIME_READY.json") `
    -Value ($frontendEvidence | ConvertTo-Json -Depth 8)

$runtimeStatePath = Join-Path $resolvedEvidenceRoot "scratch\ISOLATED_RUNTIME_STATE.json"
$runtimeState = Get-Content -LiteralPath $runtimeStatePath -Raw | ConvertFrom-Json
$runtimeState.pids | Add-Member -NotePropertyName backend -NotePropertyValue $backendProcess.Id -Force
$runtimeState.pids | Add-Member -NotePropertyName frontend -NotePropertyValue $frontendProcess.Id -Force
Write-Utf8NoBom -Path $runtimeStatePath -Value ($runtimeState | ConvertTo-Json -Depth 12)

[pscustomobject]@{
    BackendReady = $backendReady
    BackendPid = $backendProcess.Id
    FrontendReady = $frontendReady
    FrontendPid = $frontendProcess.Id
    CredentialValuesPrinted = $false
    ProductionContacted = $false
    ProductionWrites = 0
}
