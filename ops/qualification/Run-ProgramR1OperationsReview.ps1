param(
    [Parameter(Mandatory = $true)]
    [string]$EvidencePath,
    [Parameter(Mandatory = $true)]
    [string]$BackendBaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedHead,
    [Parameter(Mandatory = $true)]
    [string]$InheritedEvidenceRoot,
    [Parameter(Mandatory = $true)]
    [string]$Gate2EvidenceDirectory,
    [string]$WorktreeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
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

$resolvedEvidence = [IO.Path]::GetFullPath($EvidencePath)
if (-not $resolvedEvidence.StartsWith("C:\AgroSat_backups\PROGRAM_R1_COMPLETION_RUN\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Operations evidence must remain under the completion root."
}
$actualHead = (& git -C $WorktreeRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualHead -ne $ExpectedHead) {
    throw "Unexpected worktree HEAD."
}
$inherited = (Resolve-Path -LiteralPath $InheritedEvidenceRoot).Path
$gate2 = (Resolve-Path -LiteralPath $Gate2EvidenceDirectory).Path

$live = Invoke-RestMethod -Uri "$BackendBaseUrl/health/live" -TimeoutSec 10
$ready = Invoke-RestMethod -Uri "$BackendBaseUrl/health/ready" -TimeoutSec 10
$compatibility = Invoke-RestMethod -Uri "$BackendBaseUrl/health" -TimeoutSec 10

$mainSource = Get-Content -LiteralPath (Join-Path $WorktreeRoot "backend\main.py") -Raw
$loggingSource = Get-Content -LiteralPath (Join-Path $WorktreeRoot "backend\services\logging_config.py") -Raw
$metricsSource = Get-Content -LiteralPath (Join-Path $WorktreeRoot "backend\api\operations.py") -Raw
$configSource = Get-Content -LiteralPath (Join-Path $WorktreeRoot "backend\config.py") -Raw
$wialonRunbook = Get-Content -LiteralPath (Join-Path $WorktreeRoot "docs\TASK_209_WIALON_READ_ONLY_CONTRACT.md") -Raw

$checks = [ordered]@{
    livenessAlive = ($live.status -eq "alive")
    livenessExactRevision = ($live.release_revision -eq $ExpectedHead)
    readinessReady = ($ready.status -eq "ready")
    readinessExactRevision = ($ready.release_revision -eq $ExpectedHead)
    migrationHead0012 = ($ready.components.database.migration_revision -eq "0012_commercial_tenant_boundary")
    compatibilityHealthOk = ($compatibility.status -eq "ok")
    structuredSanitizingJsonLogs = ($loggingSource -match 'class SanitizingJsonFormatter' -and $loggingSource -match 'release_revision')
    authenticatedLowCardinalityMetrics = ($metricsSource -match 'get_current_active_user' -and $metricsSource -match 'MANAGEMENT_ROLES')
    webStartupHasNoSchedulerOrDdl = (-not ($mainSource -match 'create_all|APScheduler|BackgroundScheduler|AsyncIOScheduler|start_scheduler'))
    wialonBackendDefaultsDisabled = ($configSource -match '(?m)^\s*wialon_enabled:\s*bool\s*=\s*False\s*$')
    wialonDeferralRunbookPresent = ($wialonRunbook -match 'deferred to the next pilot wave: Integrated Operations')
    inheritedGate1EvidencePresent = ((Get-ChildItem -LiteralPath $inherited -Recurse -File | Where-Object { $_.Name -match 'GATE1|gate1' }).Count -gt 0)
    inheritedBackupRestoreReferencePresent = ((Get-ChildItem -LiteralPath $inherited -Recurse -File | Where-Object { $_.Name -match 'restore|backup' }).Count -gt 0)
    gate2CollectorEvidencePresent = ((Get-ChildItem -LiteralPath $gate2 -Recurse -File).Count -gt 0)
    rollbackContractPresent = (Test-Path -LiteralPath (Join-Path $WorktreeRoot "ops\release\rollback-contract.json"))
    releaseRunbookPresent = (Test-Path -LiteralPath (Join-Path $WorktreeRoot "ops\release\README.md"))
}
$failed = @(($checks.GetEnumerator()) | Where-Object { -not $_.Value } | ForEach-Object { $_.Key })
$report = [ordered]@{
    schemaVersion = 1
    gate = "GATE5"
    head = $actualHead
    runtimeClass = "isolated_loopback_fastapi_postgresql_postgis_redis"
    health = [ordered]@{
        liveStatus = $live.status
        readyStatus = $ready.status
        compatibilityStatus = $compatibility.status
        migrationRevision = $ready.components.database.migration_revision
        cacheStatus = $ready.components.cache.status
        collectorStatus = $ready.components.collector.status
    }
    checks = $checks
    failedChecks = $failed
    monitoring = [ordered]@{
        structuredLogs = "implemented"
        authenticatedMetrics = "implemented"
        externalErrorTracking = "not_configured_in_repository; staging integration remains separately authorized"
    }
    releaseOrder = @(
        "review immutable application and frontend artifacts",
        "backup and restore readiness confirmation",
        "apply reviewed Alembic migrations before new application processes",
        "deploy backend and verify readiness",
        "deploy frontend assets and verify critical routes",
        "enable only approved pilot feature flags",
        "start standalone collector only after explicit operational authorization"
    )
    rollbackOrder = @(
        "disable new standalone collector starts",
        "restore previous immutable frontend assets",
        "restore previous immutable backend artifact",
        "use per-revision migration classification; never auto-downgrade",
        "verify readiness and tenant-safe smoke"
    )
    featureMatrix = [ordered]@{
        coreAgronomyLoop = "enabled_first_pilot"
        offlineScouting = "enabled_first_pilot"
        sentinel = "required_live_prerequisite"
        variableRate = "human_reviewed_draft_only"
        wialon = "disabled_first_pilot_deferred_integrated_operations"
    }
    deployments = [ordered]@{ staging = $false; production = $false }
    productionWrites = 0
    status = $(if ($failed.Count -eq 0) { "PASS" } else { "FAIL" })
    marker = $(if ($failed.Count -eq 0) { "PASS_GATE5_OPERATIONS_RELEASE_SAFETY" } else { "FAIL_GATE5_OPERATIONS_RELEASE_SAFETY" })
}
Write-JsonAtomic -Path $resolvedEvidence -Value $report
Write-Output "OPERATIONS_REVIEW=$($report.status); HEAD=$actualHead; FAILED=$($failed.Count)"
if ($failed.Count -ne 0) { exit 2 }
