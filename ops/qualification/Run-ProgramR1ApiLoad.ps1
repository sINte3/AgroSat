param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,
    [Parameter(Mandatory = $true)]
    [string]$CredentialsPath,
    [Parameter(Mandatory = $true)]
    [string]$ScenarioPath,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedHead,
    [string]$BackendUrl = "http://127.0.0.1:58081",
    [int]$Concurrency = 8,
    [int]$Requests = 800
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomAtomic {
    param([string]$Path, [object]$Value)
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText(
        $temporary,
        (($Value | ConvertTo-Json -Depth 100) + [Environment]::NewLine),
        (New-Object Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$worktreeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$resolvedEvidenceRoot = (Resolve-Path -LiteralPath $EvidenceRoot).Path
$resolvedCredentials = (Resolve-Path -LiteralPath $CredentialsPath).Path
$resolvedScenarios = (Resolve-Path -LiteralPath $ScenarioPath).Path
if (-not $resolvedEvidenceRoot.StartsWith(
    "C:\AgroSat_backups\PROGRAM_R1_COMPLETION_RUN\",
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Unexpected evidence root."
}
if ($resolvedCredentials.StartsWith("C:\AgroSat_backups\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Protected credentials must remain outside evidence."
}
$actualHead = (& git -C $worktreeRoot rev-parse HEAD).Trim()
if ($actualHead -ne $ExpectedHead) {
    throw "Unexpected worktree HEAD."
}
$health = Invoke-RestMethod -Uri "$($BackendUrl.TrimEnd('/'))/health/ready" -TimeoutSec 5
if ($health.release_revision -ne $ExpectedHead) {
    throw "Isolated backend is not running the expected HEAD."
}

$credentials = Get-Content -LiteralPath $resolvedCredentials -Raw | ConvertFrom-Json
$manager = $credentials.users.manager
$login = Invoke-RestMethod `
    -Uri "$($BackendUrl.TrimEnd('/'))/api/auth/login" `
    -Method Post `
    -ContentType "application/x-www-form-urlencoded" `
    -Body @{ username = $manager.email; password = $manager.password }
if (-not $login.access_token) {
    throw "Isolated manager authentication failed."
}

$outputPath = Join-Path $resolvedEvidenceRoot "05_SECURITY_PERFORMANCE_RELEASE\API_LOAD_REPORT.json"
$previousToken = $env:TASK209_LOAD_AUTH_TOKEN
try {
    $env:TASK209_LOAD_AUTH_TOKEN = $login.access_token
    & python (Join-Path $worktreeRoot "ops\load\task209_load.py") `
        --base-url $BackendUrl `
        --scenarios $resolvedScenarios `
        --output $outputPath `
        --concurrency $Concurrency `
        --requests $Requests `
        --timeout-seconds 10
    if ($LASTEXITCODE -ne 0) {
        throw "Bounded API load harness failed."
    }
} finally {
    if ($null -eq $previousToken) {
        Remove-Item Env:TASK209_LOAD_AUTH_TOKEN -ErrorAction SilentlyContinue
    } else {
        $env:TASK209_LOAD_AUTH_TOKEN = $previousToken
    }
    $login = $null
    $manager = $null
    $credentials = $null
}

$report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json
$scenarioBudgets = @()
foreach ($scenario in @($report.scenarios)) {
    $heavy = $scenario.name -eq "vector_tile"
    $p95Budget = if ($heavy) { 2500 } else { 500 }
    $p99Budget = if ($heavy) { 2500 } else { 1000 }
    $pass = (
        [double]$scenario.p95_ms -le $p95Budget -and
        [double]$scenario.p99_ms -le $p99Budget
    )
    $scenarioBudgets += [pscustomobject]@{
        name = $scenario.name
        class = $(if ($heavy) { "heavy_local_gis" } else { "normal_json" })
        p95BudgetMs = $p95Budget
        p99BudgetMs = $p99Budget
        pass = $pass
    }
}
$errors = 0
foreach ($property in $report.error_counts.psobject.Properties) {
    $errors += [int]$property.Value
}
$processor = Get-CimInstance Win32_Processor | Select-Object -First 1
$system = Get-CimInstance Win32_ComputerSystem
$passed = (
    $report.requests_completed -eq $Requests -and
    $errors -eq 0 -and
    @($scenarioBudgets | Where-Object { -not $_.pass }).Count -eq 0
)
$report | Add-Member -NotePropertyName exactHead -NotePropertyValue $ExpectedHead -Force
$report | Add-Member -NotePropertyName runtimeClass -NotePropertyValue "isolated_loopback_postgresql_postgis_redis_fastapi" -Force
$report | Add-Member -NotePropertyName scenarioBudgets -NotePropertyValue $scenarioBudgets -Force
$report | Add-Member -NotePropertyName hardwareContext -NotePropertyValue ([pscustomobject]@{
    processorClass = $processor.Name
    logicalProcessors = [int]$system.NumberOfLogicalProcessors
    memoryGiB = [math]::Round([double]$system.TotalPhysicalMemory / 1GB, 1)
    operatingSystem = "Windows"
}) -Force
$report | Add-Member -NotePropertyName responseBodiesPersisted -NotePropertyValue $false -Force
$report | Add-Member -NotePropertyName writesPerformed -NotePropertyValue 0 -Force
$report | Add-Member -NotePropertyName productionWrites -NotePropertyValue 0 -Force
$report | Add-Member -NotePropertyName status -NotePropertyValue $(if ($passed) { "PASS" } else { "FAIL" }) -Force
$report | Add-Member -NotePropertyName marker -NotePropertyValue $(if ($passed) { "PASS_GATE5_API_LOAD_BUDGETS" } else { "FAIL_GATE5_API_LOAD_BUDGETS" }) -Force
Write-Utf8NoBomAtomic -Path $outputPath -Value $report
Write-Output ("API_LOAD={0}; REQUESTS={1}; ERRORS={2}; P95_MS={3}; P99_MS={4}; PRODUCTION_WRITES=0" -f $report.status, $report.requests_completed, $errors, $report.p95_ms, $report.p99_ms)
if (-not $passed) { exit 2 }
