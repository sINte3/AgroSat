param(
    [Parameter(Mandatory = $true)]
    [string]$EvidencePath,
    [Parameter(Mandatory = $true)]
    [string]$CredentialsPath,
    [string]$BackendUrl = "http://127.0.0.1:58081",
    [string]$WorktreeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomAtomic {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Value
    )
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw "Evidence directory is unavailable."
    }
    $temporary = Join-Path $directory (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)), [guid]::NewGuid().ToString("N"))
    [IO.File]::WriteAllText($temporary, $Value, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$resolvedEvidencePath = [IO.Path]::GetFullPath($EvidencePath)
$allowedEvidencePrefix = "C:\AgroSat_backups\PROGRAM_R1_COMPLETION_RUN\"
if (-not $resolvedEvidencePath.StartsWith($allowedEvidencePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Evidence must stay under the PROGRAM_R1 completion evidence root."
}
if (-not (Test-Path -LiteralPath $CredentialsPath -PathType Leaf)) {
    throw "Protected qualification credentials are unavailable."
}
$resolvedCredentialsPath = (Resolve-Path -LiteralPath $CredentialsPath).Path
if ($resolvedCredentialsPath.StartsWith("C:\AgroSat_backups\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Credentials must not be stored under the evidence root."
}

$credentials = Get-Content -LiteralPath $resolvedCredentialsPath -Raw | ConvertFrom-Json
$manager = $credentials.users.manager
$login = Invoke-RestMethod `
    -Uri "$($BackendUrl.TrimEnd('/'))/api/auth/login" `
    -Method Post `
    -ContentType "application/x-www-form-urlencoded" `
    -Body @{ username = $manager.email; password = $manager.password }
if (-not $login.access_token) {
    throw "Isolated manager authentication did not return an access token."
}

$routeStatus = 0
$routeSummary = [ordered]@{
    applicationStatus = $null
    provider = $null
    reason = $null
}
try {
    $response = Invoke-WebRequest `
        -Uri "$($BackendUrl.TrimEnd('/'))/api/telematics/fields/1" `
        -Method Get `
        -Headers @{ Authorization = "Bearer $($login.access_token)" } `
        -UseBasicParsing
    $routeStatus = [int]$response.StatusCode
    $payload = $response.Content | ConvertFrom-Json
    $routeSummary.applicationStatus = $payload.status
    $routeSummary.provider = $payload.provider
    $routeSummary.reason = $payload.reason
} catch {
    if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
        $routeStatus = [int]$_.Exception.Response.StatusCode
    } else {
        throw
    }
}

$configText = Get-Content -LiteralPath (Join-Path $WorktreeRoot "backend\config.py") -Raw
$apiText = Get-Content -LiteralPath (Join-Path $WorktreeRoot "backend\api\telematics.py") -Raw
$fieldDetailText = Get-Content -LiteralPath (Join-Path $WorktreeRoot "frontend\src\components\Field\FieldDetail.jsx") -Raw
$frontendFeaturePath = Join-Path $WorktreeRoot "frontend\src\config\pilotFeatures.js"
$frontendFeatureText = if (Test-Path -LiteralPath $frontendFeaturePath -PathType Leaf) {
    Get-Content -LiteralPath $frontendFeaturePath -Raw
} else {
    ""
}

$backendDefaultDisabled = $configText -match '(?m)^\s*wialon_enabled:\s*bool\s*=\s*False\s*$'
$backendRouteEnforced = (
    $apiText -match 'settings\.wialon_enabled' -and
    $apiText -match 'HTTPException' -and
    $routeStatus -eq 404
)
$frontendDefaultDisabled = (
    $frontendFeatureText -match "VITE_WIALON_ENABLED" -and
    $frontendFeatureText -match "===\s*'true'"
)
$frontendUiEnforced = (
    $fieldDetailText -match 'FIRST_PILOT_FEATURES\.wialon' -and
    $fieldDetailText -match "activeTab\s*===\s*'telematics'"
)
$passed = $backendDefaultDisabled -and $backendRouteEnforced -and $frontendDefaultDisabled -and $frontendUiEnforced
$head = (& git -C $WorktreeRoot rev-parse HEAD).Trim()

$evidence = [ordered]@{
    schemaVersion = 1
    recordedAt = [DateTimeOffset]::UtcNow.ToString("o")
    exactHead = $head
    runtimeClass = "isolated_ephemeral"
    firstPilotScope = "wialon_deferred_to_next_pilot_integrated_operations"
    liveWialonRequestInitiatedByProbe = $false
    wialonTokenOrMappingSupplied = $false
    backend = [ordered]@{
        settingDeclaredDefaultFalse = $backendDefaultDisabled
        disabledRouteEnforced = $backendRouteEnforced
        isolatedRouteHttpStatus = $routeStatus
        sanitizedApplicationStatus = $routeSummary.applicationStatus
        sanitizedProviderClass = $routeSummary.provider
        sanitizedReason = $routeSummary.reason
    }
    frontend = [ordered]@{
        compileTimeFlagDeclaredDefaultFalse = $frontendDefaultDisabled
        fieldDetailUiGuardPresent = $frontendUiEnforced
    }
    decision = if ($passed) { "PASS_WIALON_FIRST_PILOT_DISABLED_BOUNDARY" } else { "FAIL_WIALON_FIRST_PILOT_DISABLED_BOUNDARY" }
    credentialsPrinted = $false
    productionContacted = $false
    productionWrites = 0
}

Write-Utf8NoBomAtomic -Path $resolvedEvidencePath -Value ($evidence | ConvertTo-Json -Depth 10)
Write-Output ("WIALON_BOUNDARY={0}; HTTP_STATUS={1}; PRODUCTION_WRITES=0" -f $evidence.decision, $routeStatus)
