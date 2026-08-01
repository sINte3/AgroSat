[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedHead,

    [Parameter(Mandatory = $true)]
    [string]$RedisRuntimeSource,

    [string]$TemporaryRoot = "C:\tmp\agrosat_r1_completion_runtime",
    [string]$PostgresBin = "C:\Program Files\PostgreSQL\16\bin",
    [int]$PostgresPort = 55439,
    [int]$RedisPort = 56381,
    [int]$BackendPort = 58081,
    [int]$FrontendPort = 54181,
    [int]$ProviderStubPort = 59081
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AllowedTemporaryPrefix = "C:\tmp\agrosat_r1_completion_"

function New-QualificationSecret {
    param([int]$ByteCount = 32)

    $bytes = New-Object byte[] $ByteCount
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Assert-PortAvailable {
    param([int]$Port)

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($null -ne $listener) {
        throw "Qualification port $Port is already in use."
    }
}

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

$resolvedEvidenceRoot = (Resolve-Path -LiteralPath $EvidenceRoot).Path
$stagePath = Join-Path $resolvedEvidenceRoot "scratch\INFRASTRUCTURE_CURRENT_STAGE.txt"
function Write-QualificationStage {
    param([string]$Stage)

    Write-Utf8NoBom -Path $stagePath -Value $Stage
}

if (-not $TemporaryRoot.StartsWith($AllowedTemporaryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "TemporaryRoot must remain under the dedicated qualification prefix."
}
if (Test-Path -LiteralPath $TemporaryRoot) {
    throw "TemporaryRoot already exists: $TemporaryRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $resolvedEvidenceRoot "scratch") -PathType Container)) {
    throw "Evidence scratch directory is missing."
}
if (-not (Test-Path -LiteralPath (Join-Path $PostgresBin "initdb.exe") -PathType Leaf)) {
    throw "PostgreSQL 16 binaries are unavailable."
}
$resolvedRedisRuntimeSource = (Resolve-Path -LiteralPath $RedisRuntimeSource).Path
if (-not (Test-Path -LiteralPath (Join-Path $resolvedRedisRuntimeSource "memurai.exe") -PathType Leaf)) {
    throw "The approved Redis-compatible runtime is unavailable."
}

$currentHead = (git rev-parse HEAD).Trim()
if ($currentHead -ne $ExpectedHead) {
    throw "Unexpected qualification HEAD: $currentHead"
}
if (git status --porcelain=v1 --untracked-files=no) {
    throw "Tracked files were already modified before qualification harness creation."
}

@($PostgresPort, $RedisPort, $BackendPort, $FrontendPort, $ProviderStubPort) |
    ForEach-Object { Assert-PortAvailable -Port $_ }

New-Item -ItemType Directory -Path $TemporaryRoot | Out-Null
New-Item -ItemType Directory -Path (Join-Path $TemporaryRoot "postgres-data") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $TemporaryRoot "memurai") | Out-Null
Write-QualificationStage -Stage "temporary_directories_created"

# Credentials and runtime configuration inherit access only for the current user.
icacls $TemporaryRoot /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null

$nonce = (New-QualificationSecret -ByteCount 8).Substring(0, 10).ToLowerInvariant()
$credentials = [ordered]@{
    dbUser = "agrosat_r1_owner"
    dbPassword = New-QualificationSecret
    redisPassword = New-QualificationSecret
    jwtSecret = New-QualificationSecret -ByteCount 48
    users = [ordered]@{
        admin = [ordered]@{
            email = "r1-admin-$nonce@invalid.local"
            password = New-QualificationSecret -ByteCount 24
        }
        manager = [ordered]@{
            email = "r1-manager-$nonce@invalid.local"
            password = New-QualificationSecret -ByteCount 24
        }
        agronomist = [ordered]@{
            email = "r1-agronomist-$nonce@invalid.local"
            password = New-QualificationSecret -ByteCount 24
        }
        viewer = [ordered]@{
            email = "r1-viewer-$nonce@invalid.local"
            password = New-QualificationSecret -ByteCount 24
        }
        disabled = [ordered]@{
            email = "r1-disabled-$nonce@invalid.local"
            password = New-QualificationSecret -ByteCount 24
        }
    }
}

$credentialPath = Join-Path $TemporaryRoot "credentials.json"
Write-Utf8NoBom -Path $credentialPath -Value ($credentials | ConvertTo-Json -Depth 8)
Write-QualificationStage -Stage "ephemeral_credentials_created"

$passwordFile = Join-Path $TemporaryRoot "postgres-superuser.pw"
Write-Utf8NoBom -Path $passwordFile -Value $credentials.dbPassword
$postgresInitLog = Join-Path $resolvedEvidenceRoot "scratch\POSTGRES_INIT.txt"

& (Join-Path $PostgresBin "initdb.exe") `
    -D (Join-Path $TemporaryRoot "postgres-data") `
    --username=$($credentials.dbUser) `
    --pwfile=$passwordFile `
    --auth-host=scram-sha-256 `
    --auth-local=scram-sha-256 `
    --encoding=UTF8 `
    --locale=C *> $postgresInitLog
if ($LASTEXITCODE -ne 0) {
    throw "initdb failed with exit code $LASTEXITCODE."
}
Remove-Item -LiteralPath $passwordFile -Force
Write-QualificationStage -Stage "postgres_initialized"

$postgresServerLog = Join-Path $TemporaryRoot "postgres-server.log"
$postgresData = Join-Path $TemporaryRoot "postgres-data"
Add-Content -LiteralPath (Join-Path $postgresData "postgresql.conf") -Encoding UTF8 -Value @(
    "port = $PostgresPort",
    "listen_addresses = '127.0.0.1'"
)
$pgCtlOutput = Join-Path $TemporaryRoot "pg-ctl-start.out"
$pgCtlError = Join-Path $TemporaryRoot "pg-ctl-start.err"
$pgCtlProcess = Start-Process `
    -FilePath (Join-Path $PostgresBin "pg_ctl.exe") `
    -ArgumentList @("-D", $postgresData, "-l", $postgresServerLog, "-t", "30", "-w", "start") `
    -RedirectStandardOutput $pgCtlOutput `
    -RedirectStandardError $pgCtlError `
    -WindowStyle Hidden `
    -PassThru
$postgresReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt += 1) {
    Start-Sleep -Milliseconds 250
    & (Join-Path $PostgresBin "pg_isready.exe") `
        -h 127.0.0.1 `
        -p $PostgresPort `
        -U $credentials.dbUser `
        -d postgres *> $null
    if ($LASTEXITCODE -eq 0) {
        $postgresReady = $true
        break
    }
    if ($pgCtlProcess.HasExited -and $pgCtlProcess.ExitCode -ne 0) {
        break
    }
}
if (-not $postgresReady) {
    throw "PostgreSQL did not become ready."
}
Write-QualificationStage -Stage "postgres_started"

$env:PGPASSWORD = $credentials.dbPassword
try {
    & (Join-Path $PostgresBin "createdb.exe") `
        -h 127.0.0.1 `
        -p $PostgresPort `
        -U $credentials.dbUser `
        --no-password `
        agrosat_r1_completion
    if ($LASTEXITCODE -ne 0) {
        throw "Isolated database creation failed."
    }
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
Write-QualificationStage -Stage "database_created"

Copy-Item -Path (Join-Path $resolvedRedisRuntimeSource "*") -Destination (Join-Path $TemporaryRoot "memurai") -Recurse -Force
Write-QualificationStage -Stage "redis_runtime_copied"
$temporaryForwardSlashes = $TemporaryRoot.Replace("\", "/")
$memuraiConfiguration = @(
    "bind 127.0.0.1",
    "protected-mode yes",
    "port $RedisPort",
    "timeout 0",
    "tcp-keepalive 60",
    "daemonize no",
    "supervised no",
    "loglevel notice",
    "logfile $temporaryForwardSlashes/memurai-server.log",
    "databases 16",
    'save ""',
    "appendonly no",
    "dir $temporaryForwardSlashes",
    "requirepass $($credentials.redisPassword)"
)
$memuraiConfigurationPath = Join-Path $TemporaryRoot "memurai.conf"
Write-Utf8NoBom -Path $memuraiConfigurationPath -Value ($memuraiConfiguration -join [Environment]::NewLine)

$memuraiProcess = Start-Process `
    -FilePath (Join-Path $TemporaryRoot "memurai\memurai.exe") `
    -ArgumentList @($memuraiConfigurationPath) `
    -WindowStyle Hidden `
    -PassThru

$redisReady = $false
$env:REDISCLI_AUTH = $credentials.redisPassword
try {
    for ($attempt = 0; $attempt -lt 30; $attempt += 1) {
        Start-Sleep -Milliseconds 200
        $pong = & (Join-Path $TemporaryRoot "memurai\memurai-cli.exe") `
            -h 127.0.0.1 `
            -p $RedisPort `
            ping 2>$null
        if ($pong -eq "PONG") {
            $redisReady = $true
            break
        }
    }
}
finally {
    Remove-Item Env:\REDISCLI_AUTH -ErrorAction SilentlyContinue
}
if (-not $redisReady) {
    throw "Redis-compatible runtime did not become ready."
}
Write-QualificationStage -Stage "redis_started"

$databaseUrl = "postgresql://$($credentials.dbUser):$($credentials.dbPassword)@127.0.0.1:$PostgresPort/agrosat_r1_completion"
$redisUrl = "redis://:$($credentials.redisPassword)@127.0.0.1:$RedisPort/15"
$runtimeEnvironment = @(
    "APP_NAME=AgroSat R1 Qualification",
    "APP_VERSION=0.1.0",
    "RELEASE_REVISION=$ExpectedHead",
    "ENVIRONMENT=qualification",
    "DEBUG=false",
    "PUBLIC_REGISTRATION_ENABLED=false",
    "WIALON_ENABLED=false",
    "SECRET_KEY=$($credentials.jwtSecret)",
    "DATABASE_URL=$databaseUrl",
    "REDIS_URL=$redisUrl",
    "COLLECTOR_STATUS_DIRECTORY=",
    "WEATHER_API_URL=http://127.0.0.1:$ProviderStubPort/weather",
    "TELEGRAM_NOTIFICATIONS_ENABLED=false",
    "SENTINEL_HUB_CLIENT_ID=",
    "SENTINEL_HUB_CLIENT_SECRET=",
    "ANTHROPIC_API_KEY=",
    "TELEGRAM_BOT_TOKEN=",
    "TELEGRAM_CHAT_ID="
)
$runtimeEnvironmentPath = Join-Path $TemporaryRoot "runtime.env"
Write-Utf8NoBom -Path $runtimeEnvironmentPath -Value ($runtimeEnvironment -join [Environment]::NewLine)
Write-QualificationStage -Stage "runtime_environment_created"

$postgresPid = Get-NetTCPConnection -LocalPort $PostgresPort -State Listen |
    Select-Object -First 1 -ExpandProperty OwningProcess
$runtimeState = [ordered]@{
    createdAt = (Get-Date).ToString("o")
    head = $ExpectedHead
    temporaryRoot = $TemporaryRoot
    credentialFileClass = "protected_ephemeral_excluded_from_evidence"
    runtimeEnvironmentClass = "protected_ephemeral_excluded_from_evidence"
    ports = [ordered]@{
        postgres = $PostgresPort
        redis = $RedisPort
        backend = $BackendPort
        frontend = $FrontendPort
        providerStub = $ProviderStubPort
    }
    pids = [ordered]@{
        postgres = $postgresPid
        redis = $memuraiProcess.Id
    }
    versions = [ordered]@{
        postgres = "16.14"
        postgis = "pending_migration_probe"
        redisCompatible = "Memurai 4.1.7 API 7.2.11"
    }
    bindings = [ordered]@{
        postgres = "127.0.0.1"
        redis = "127.0.0.1"
    }
    productionContacted = $false
    productionWrites = 0
}
$runtimeStatePath = Join-Path $resolvedEvidenceRoot "scratch\ISOLATED_RUNTIME_STATE.json"
Write-Utf8NoBom -Path $runtimeStatePath -Value ($runtimeState | ConvertTo-Json -Depth 8)
Write-QualificationStage -Stage "infrastructure_ready"

$postgresReady = (& (Join-Path $PostgresBin "pg_isready.exe") `
    -h 127.0.0.1 `
    -p $PostgresPort `
    -U $credentials.dbUser `
    -d agrosat_r1_completion) -match "accepting connections"

[pscustomobject]@{
    TemporaryRootCreated = $true
    PostgresReady = $postgresReady
    RedisReady = $redisReady
    PostgresPid = $postgresPid
    RedisPid = $memuraiProcess.Id
    CredentialValuesPrinted = $false
    ProductionContacted = $false
    ProductionWrites = 0
}
