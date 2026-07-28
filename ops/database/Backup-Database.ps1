param(
    [Parameter(Mandatory = $true)]
    [string]$DatabaseName,
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [ValidateSet("isolated", "review", "production-like")]
    [string]$ServerClass = "review",
    [string]$ServerName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 5432,
    [switch]$ConfirmReadOnlySource,
    [switch]$Apply
)

. "$PSScriptRoot\Task209-Database.Common.ps1"

Assert-SafeDatabaseName -DatabaseName $DatabaseName
$resolvedOutput = Resolve-Task209EvidencePath -PathValue $OutputDirectory

$preview = [ordered]@{
    schema_version = 1
    status = "preview"
    operation = "schema_and_data_backup"
    database_name = $DatabaseName
    server_class = $ServerClass
    output_directory = $resolvedOutput
    source_confirmed_read_only = [bool]$ConfirmReadOnlySource
    database_mutation = $false
}

if (-not $Apply) {
    $preview | ConvertTo-Json
    return
}
if (-not $ConfirmReadOnlySource) {
    throw "Apply requires an explicit read-only source confirmation."
}
Assert-OutsideSanitizedEvidence -PathValue $resolvedOutput

$pgDump = Require-DatabaseTool -Name "pg_dump"
$pgRestore = Require-DatabaseTool -Name "pg_restore"
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$backupName = "$DatabaseName-$timestamp.dump"
$backupPath = Join-Path $resolvedOutput $backupName
$restoreListPath = Join-Path $resolvedOutput "$backupName.restore-list.txt"
$checksumPath = Join-Path $resolvedOutput "$backupName.sha256.txt"
$metadataPath = Join-Path $resolvedOutput "$backupName.metadata.json"

& $pgDump @(
    "--host=$ServerName",
    "--port=$Port",
    "--dbname=$DatabaseName",
    "--format=custom",
    "--no-owner",
    "--no-acl",
    "--no-password",
    "--file=$backupPath"
) 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Database backup failed with a nonzero exit code."
}

& $pgRestore @("--list", $backupPath) 2>$null |
    Set-Content -LiteralPath $restoreListPath -Encoding utf8
if ($LASTEXITCODE -ne 0) {
    throw "Restore-list validation failed with a nonzero exit code."
}

$restoreEntries = @(
    Get-Content -LiteralPath $restoreListPath |
        Where-Object { $_ -and -not $_.StartsWith(";") }
)
if ($restoreEntries.Count -lt 1) {
    throw "Restore-list validation returned no entries."
}

$hash = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $backupName" |
    Set-Content -LiteralPath $checksumPath -Encoding ascii

$metadata = [ordered]@{
    schema_version = 1
    status = "pass"
    operation = "schema_and_data_backup"
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    database_name = $DatabaseName
    server_class = $ServerClass
    backup_file = $backupName
    sha256 = $hash
    restore_list_entry_count = $restoreEntries.Count
    owner_acl_excluded = $true
    database_mutation = $false
}
Write-SanitizedJsonFile -Path $metadataPath -Value $metadata
$metadata | ConvertTo-Json
