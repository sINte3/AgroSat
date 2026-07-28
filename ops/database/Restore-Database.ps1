param(
    [Parameter(Mandatory = $true)]
    [string]$DatabaseName,
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedSha256,
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [string]$ServerName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 5432,
    [string]$ExpectedMigrationRevision = "",
    [switch]$Apply
)

. "$PSScriptRoot\Task209-Database.Common.ps1"

Assert-SafeDatabaseName -DatabaseName $DatabaseName -RequireTask209Isolation
$resolvedBackup = Resolve-Task209EvidencePath -PathValue $BackupPath
$resolvedOutput = Resolve-Task209EvidencePath -PathValue $OutputDirectory
if ($ExpectedSha256 -notmatch "^[a-fA-F0-9]{64}$") {
    throw "Expected SHA-256 does not satisfy the checksum contract."
}

$preview = [ordered]@{
    schema_version = 1
    status = "preview"
    operation = "isolated_database_restore"
    database_name = $DatabaseName
    backup_file = Split-Path -Leaf $resolvedBackup
    output_directory = $resolvedOutput
    target_isolation_enforced = $true
    automatic_drop_on_failure = $false
    database_mutation = $true
}
if (-not $Apply) {
    $preview | ConvertTo-Json
    return
}
if (-not (Test-Path -LiteralPath $resolvedBackup -PathType Leaf)) {
    throw "Backup file is unavailable."
}
Assert-OutsideSanitizedEvidence -PathValue $resolvedBackup
$actualHash = (
    Get-FileHash -LiteralPath $resolvedBackup -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($actualHash -ne $ExpectedSha256.ToLowerInvariant()) {
    throw "Backup checksum validation failed."
}

$psql = Require-DatabaseTool -Name "psql"
$createdb = Require-DatabaseTool -Name "createdb"
$pgRestore = Require-DatabaseTool -Name "pg_restore"

$existing = @(
    Invoke-CapturedDatabaseTool -Executable $psql -Arguments @(
        "--host=$ServerName",
        "--port=$Port",
        "--dbname=postgres",
        "--no-password",
        "--no-align",
        "--tuples-only",
        "--set=ON_ERROR_STOP=1",
        "--command=SELECT 1 FROM pg_database WHERE datname = '$DatabaseName';"
    )
)
if (@($existing | Where-Object { $_.Trim() -eq "1" }).Count -ne 0) {
    throw "Isolated restore target already exists; refusing to overwrite it."
}

$restoreList = @(
    Invoke-CapturedDatabaseTool -Executable $pgRestore -Arguments @(
        "--list",
        $resolvedBackup
    )
)
$restoreEntries = @(
    $restoreList | Where-Object { $_ -and -not $_.StartsWith(";") }
)
if ($restoreEntries.Count -lt 1) {
    throw "Restore-list validation returned no entries."
}

& $createdb @(
    "--host=$ServerName",
    "--port=$Port",
    "--no-password",
    $DatabaseName
) 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Isolated database creation failed with a nonzero exit code."
}

& $pgRestore @(
    "--host=$ServerName",
    "--port=$Port",
    "--dbname=$DatabaseName",
    "--no-password",
    "--exit-on-error",
    "--single-transaction",
    "--no-owner",
    "--no-acl",
    $resolvedBackup
) 2>$null
if ($LASTEXITCODE -ne 0) {
    throw (
        "Isolated restore failed. The exact partial target was retained " +
        "for explicit operator inspection; no automatic drop was attempted."
    )
}

& "$PSScriptRoot\Validate-RestoredDatabase.ps1" `
    -DatabaseName $DatabaseName `
    -OutputDirectory $resolvedOutput `
    -ServerClass "isolated" `
    -ServerName $ServerName `
    -Port $Port `
    -ExpectedMigrationRevision $ExpectedMigrationRevision `
    -Apply
if ($LASTEXITCODE -ne 0) {
    throw "Post-restore validation failed."
}
