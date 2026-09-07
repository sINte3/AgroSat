[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [ValidateSet('Rehearsal', 'Production')][string]$Mode = 'Rehearsal',
    [string]$AuthorizationPath = '',
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ReleaseCandidate,
    [string]$ExpectedBranch = 'task/program-r3-macrostage-f-closed-loop-agronomy',
    [Parameter(Mandatory = $true)][string]$RehearsalRoot,
    [Parameter(Mandatory = $true)][string]$RehearsalDatabase,
    [Parameter(Mandatory = $true)][string]$SourceArchive,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$SourceArchiveSha256,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$DatabaseMigrationScript,
    [string]$HealthCheckScript = '',
    [string]$HealthBaseUrl = '',
    [string]$ValidatedBackup = '',
    [string]$ValidatedBackupSha256 = '',
    [string]$BackupIdentityPath = '',
    [string]$DatabaseRestoreScript = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-ValidatedManifest {
    param([string]$Path, [string]$Candidate, [string]$ArchiveHash)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'RELEASE_MANIFEST_MISSING' }
    $manifest = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    foreach ($field in @('release_candidate', 'program_head', 'origin_program_head', 'program_worktree_clean', 'origin_aligned', 'source_main_unchanged', 'source_archive_sha256')) {
        if ($null -eq $manifest.PSObject.Properties[$field]) { throw "RELEASE_MANIFEST_FIELD_MISSING:$field" }
    }
    if ($manifest.release_candidate -cne $Candidate -or $manifest.program_head -cne $Candidate -or $manifest.origin_program_head -cne $Candidate) { throw 'RELEASE_MANIFEST_CANDIDATE_MISMATCH' }
    if (-not $manifest.program_worktree_clean -or -not $manifest.origin_aligned -or -not $manifest.source_main_unchanged) { throw 'RELEASE_MANIFEST_SAFETY_MISMATCH' }
    if ($manifest.source_archive_sha256 -cne $ArchiveHash) { throw 'RELEASE_MANIFEST_ARCHIVE_HASH_MISMATCH' }
    return $manifest
}

$preflight = Join-Path $PSScriptRoot 'Test-AgroSatProductionPreflight.ps1'
& $preflight -Mode $Mode -AuthorizationPath $AuthorizationPath -ReleaseCandidate $ReleaseCandidate -RehearsalRoot $RehearsalRoot -RehearsalDatabase $RehearsalDatabase | Out-Null
if ($Mode -eq 'Production') { throw 'PRODUCTION_RELEASE_FAIL_CLOSED' }
if (($HealthCheckScript -and -not $HealthBaseUrl) -or (-not $HealthCheckScript -and $HealthBaseUrl)) { throw 'HEALTH_CHECK_IDENTITY_INCOMPLETE' }
if ($HealthCheckScript -and ((-not $ValidatedBackup) -or (-not $ValidatedBackupSha256) -or (-not $BackupIdentityPath) -or (-not $DatabaseRestoreScript))) { throw 'HEALTH_FAILURE_RECOVERY_IDENTITY_INCOMPLETE' }
if (-not (Test-Path -LiteralPath $SourceArchive -PathType Leaf)) { throw 'SOURCE_ARCHIVE_MISSING' }
$actualArchiveHash = (Get-FileHash -LiteralPath $SourceArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualArchiveHash -cne $SourceArchiveSha256.ToLowerInvariant()) { throw 'SOURCE_ARCHIVE_HASH_MISMATCH' }
$null = Read-ValidatedManifest -Path $ManifestPath -Candidate $ReleaseCandidate -ArchiveHash $actualArchiveHash

$root = [IO.Path]::GetFullPath($RehearsalRoot)
$releaseDirectory = Join-Path $root (Join-Path 'release' $ReleaseCandidate)
$pointerPath = Join-Path $root 'current-release.json'
$previousPointerPath = Join-Path $root 'previous-release.json'
$migrationScript = [IO.Path]::GetFullPath($DatabaseMigrationScript)
if (-not $migrationScript.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'DATABASE_MIGRATION_SCRIPT_OUTSIDE_REHEARSAL_ROOT' }
if (-not (Test-Path -LiteralPath $migrationScript -PathType Leaf)) { throw 'DATABASE_MIGRATION_SCRIPT_MISSING' }
if (Test-Path -LiteralPath $releaseDirectory) { throw 'IMMUTABLE_RELEASE_ALREADY_EXISTS' }
if ($PSCmdlet.ShouldProcess($releaseDirectory, 'materialize immutable isolated release and switch isolated pointer')) {
    $archiveValidator = Join-Path $PSScriptRoot 'Test-AgroSatReleaseArchive.ps1'
    & $archiveValidator -ArchivePath $SourceArchive -ExpectedSha256 $actualArchiveHash -ReleaseCandidate $ReleaseCandidate -ExpectedBranch $ExpectedBranch -DestinationPath $releaseDirectory | Out-Null
    if (-not (Test-Path -LiteralPath $releaseDirectory -PathType Container)) { throw 'IMMUTABLE_RELEASE_MATERIALIZATION_FAILED' }
    & $migrationScript -RehearsalDatabase $RehearsalDatabase -ReleaseDirectory $releaseDirectory -ReleaseCandidate $ReleaseCandidate
    if (-not $?) { throw 'ISOLATED_DATABASE_MIGRATION_FAILED' }
    if (Test-Path -LiteralPath $pointerPath -PathType Leaf) { Copy-Item -LiteralPath $pointerPath -Destination $previousPointerPath -Force -ErrorAction Stop }
    [ordered]@{ release_candidate = $ReleaseCandidate; release_directory = $releaseDirectory; archive_sha256 = $actualArchiveHash; switched_at_utc = (Get-Date).ToUniversalTime().ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $pointerPath -Encoding utf8
    if ($HealthCheckScript) {
        $healthScript = [IO.Path]::GetFullPath($HealthCheckScript)
        if (-not $healthScript.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $healthScript -PathType Leaf)) { throw 'HEALTH_CHECK_SCRIPT_OUTSIDE_REHEARSAL_ROOT' }
        try {
            & $healthScript -BaseUrl $HealthBaseUrl -ReleaseCandidate $ReleaseCandidate | Out-Null
            if (-not $?) { throw 'ISOLATED_POST_SWITCH_HEALTH_CHECK_FAILED' }
        } catch {
            if (-not (Test-Path -LiteralPath $ValidatedBackup -PathType Leaf)) { throw 'HEALTH_FAILURE_BACKUP_MISSING' }
            $backupHash = (Get-FileHash -LiteralPath $ValidatedBackup -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($backupHash -cne $ValidatedBackupSha256.ToLowerInvariant()) { throw 'HEALTH_FAILURE_BACKUP_HASH_MISMATCH' }
            if (-not (Test-Path -LiteralPath $BackupIdentityPath -PathType Leaf)) { throw 'HEALTH_FAILURE_BACKUP_IDENTITY_MISSING' }
            try { $backupIdentity = Get-Content -LiteralPath $BackupIdentityPath -Raw | ConvertFrom-Json } catch { throw 'HEALTH_FAILURE_BACKUP_IDENTITY_MALFORMED' }
            foreach ($field in @('backup_sha256', 'rehearsal_database')) {
                if ($null -eq $backupIdentity.PSObject.Properties[$field]) { throw "HEALTH_FAILURE_BACKUP_IDENTITY_FIELD_MISSING:$field" }
            }
            if ([string]$backupIdentity.backup_sha256 -notmatch '^[a-fA-F0-9]{64}$' -or ([string]$backupIdentity.backup_sha256).ToLowerInvariant() -cne $backupHash) { throw 'HEALTH_FAILURE_BACKUP_IDENTITY_SHA_MISMATCH' }
            if ([string]$backupIdentity.rehearsal_database -cne $RehearsalDatabase) { throw 'HEALTH_FAILURE_BACKUP_IDENTITY_DATABASE_MISMATCH' }
            $restoreScript = [IO.Path]::GetFullPath($DatabaseRestoreScript)
            if (-not $restoreScript.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $restoreScript -PathType Leaf)) { throw 'HEALTH_FAILURE_RESTORE_SCRIPT_OUTSIDE_REHEARSAL_ROOT' }
            try {
                & $restoreScript -RehearsalDatabase $RehearsalDatabase -ValidatedBackup $ValidatedBackup -ReleaseCandidate $ReleaseCandidate
                if (-not $?) { throw 'DUMMY_OR_NATIVE_RESTORE_FAILURE' }
            } catch { throw 'HEALTH_FAILURE_DATABASE_RESTORE_FAILED_MANUAL_RECOVERY_REQUIRED' }
            if (Test-Path -LiteralPath $previousPointerPath -PathType Leaf) { Copy-Item -LiteralPath $previousPointerPath -Destination $pointerPath -Force -ErrorAction Stop } else { Remove-Item -LiteralPath $pointerPath -Force -ErrorAction Stop }
            throw 'ISOLATED_POST_SWITCH_HEALTH_FAILED_ROLLED_BACK'
        }
    }
}
[ordered]@{ status = 'PASS'; mode = 'Rehearsal'; operation = 'isolated_release_materialized_database_migrated_and_pointer_switched'; release_candidate = $ReleaseCandidate; release_directory = $releaseDirectory; current_pointer = $pointerPath; archive_sha256 = $actualArchiveHash; isolated_database_migration_executed = (-not $WhatIfPreference); mutation_performed = (-not $WhatIfPreference); production_or_staging_mutated = $false } | ConvertTo-Json
