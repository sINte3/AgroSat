param(
    [Parameter(Mandatory = $true)]
    [string]$DatabaseName,
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [ValidateSet("isolated", "review")]
    [string]$ServerClass = "isolated",
    [string]$ServerName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 5432,
    [string]$ExpectedMigrationRevision = "",
    [switch]$Apply
)

. "$PSScriptRoot\Task209-Database.Common.ps1"

Assert-SafeDatabaseName -DatabaseName $DatabaseName -RequireTask209Isolation
$resolvedOutput = Resolve-Task209EvidencePath -PathValue $OutputDirectory

$preview = [ordered]@{
    schema_version = 1
    status = "preview"
    operation = "restored_database_validation"
    database_name = $DatabaseName
    server_class = $ServerClass
    output_directory = $resolvedOutput
    read_only = $true
}
if (-not $Apply) {
    $preview | ConvertTo-Json
    return
}

$psql = Require-DatabaseTool -Name "psql"
$pgDump = Require-DatabaseTool -Name "pg_dump"
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

function Invoke-ScalarQuery {
    param([Parameter(Mandatory = $true)][string]$Sql)

    $result = @(
        Invoke-CapturedDatabaseTool -Executable $psql -Arguments @(
            "--host=$ServerName",
            "--port=$Port",
            "--dbname=$DatabaseName",
            "--no-password",
            "--no-align",
            "--tuples-only",
            "--set=ON_ERROR_STOP=1",
            "--command=$Sql"
        )
    )
    if ($result.Count -ne 1) {
        throw "Database validation query returned an unexpected shape."
    }
    return $result[0].Trim()
}

$migrationRevision = Invoke-ScalarQuery -Sql (
    "SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 1;"
)
if (-not $migrationRevision) {
    throw "Restored database has no Alembic revision."
}
if (
    $ExpectedMigrationRevision -and
    $migrationRevision -ne $ExpectedMigrationRevision
) {
    throw "Restored database migration revision does not match the expected value."
}

$criticalTables = @(
    "enterprises",
    "fields",
    "users",
    "crop_types",
    "crop_seasons",
    "ndvi_records",
    "satellite_index_records",
    "alerts",
    "scouting_notes",
    "field_inspections"
)
$tableCounts = [ordered]@{}
foreach ($tableName in $criticalTables) {
    $count = Invoke-ScalarQuery -Sql "SELECT count(*)::bigint FROM public.$tableName;"
    $tableCounts[$tableName] = [long]$count
}

$invalidConstraints = [int](Invoke-ScalarQuery -Sql (
    "SELECT count(*) FROM pg_constraint " +
    "WHERE connamespace = 'public'::regnamespace AND NOT convalidated;"
))
$invalidIndexes = [int](Invoke-ScalarQuery -Sql (
    "SELECT count(*) FROM pg_index i " +
    "JOIN pg_class c ON c.oid = i.indexrelid " +
    "JOIN pg_namespace n ON n.oid = c.relnamespace " +
    "WHERE n.nspname = 'public' AND NOT i.indisvalid;"
))
if ($invalidConstraints -ne 0 -or $invalidIndexes -ne 0) {
    throw "Restored database contains invalid constraints or indexes."
}

$schemaPath = Join-Path $resolvedOutput "$DatabaseName.schema.sql"
& $pgDump @(
    "--host=$ServerName",
    "--port=$Port",
    "--dbname=$DatabaseName",
    "--schema-only",
    "--no-owner",
    "--no-acl",
    "--no-password",
    "--file=$schemaPath"
) 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Restored schema hashing failed with a nonzero exit code."
}
$schemaHash = (
    Get-FileHash -LiteralPath $schemaPath -Algorithm SHA256
).Hash.ToLowerInvariant()

$report = [ordered]@{
    schema_version = 1
    status = "pass"
    operation = "restored_database_validation"
    validated_at = (Get-Date).ToUniversalTime().ToString("o")
    database_name = $DatabaseName
    server_class = $ServerClass
    migration_revision = $migrationRevision
    table_counts = $tableCounts
    invalid_constraint_count = $invalidConstraints
    invalid_index_count = $invalidIndexes
    schema_sha256 = $schemaHash
    read_only = $true
}
$reportPath = Join-Path $resolvedOutput "$DatabaseName.validation.json"
Write-SanitizedJsonFile -Path $reportPath -Value $report
$report | ConvertTo-Json -Depth 8
