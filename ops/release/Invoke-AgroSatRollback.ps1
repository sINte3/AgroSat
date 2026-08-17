[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [ValidateSet('Rehearsal', 'Production')][string]$Mode = 'Rehearsal',
    [string]$AuthorizationPath = '',
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ReleaseCandidate,
    [Parameter(Mandatory = $true)][string]$RehearsalRoot,
    [Parameter(Mandatory = $true)][string]$RehearsalDatabase,
    [Parameter(Mandatory = $true)][string]$ValidatedBackup,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ValidatedBackupSha256,
    [Parameter(Mandatory = $true)][string]$BackupIdentityPath,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$DatabaseRestoreScript,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedPreviousArchiveSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-RequiredJson {
    param([string]$Path, [string]$MissingCode)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw $MissingCode }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } catch { throw "$MissingCode`:MALFORMED" }
}

$preflight = Join-Path $PSScriptRoot 'Test-AgroSatProductionPreflight.ps1'
& $preflight -Mode $Mode -AuthorizationPath $AuthorizationPath -ReleaseCandidate $ReleaseCandidate -RehearsalRoot $RehearsalRoot -RehearsalDatabase $RehearsalDatabase | Out-Null
if ($Mode -eq 'Production') { throw 'PRODUCTION_ROLLBACK_FAIL_CLOSED' }
if (-not (Test-Path -LiteralPath $ValidatedBackup -PathType Leaf)) { throw 'REHEARSAL_BACKUP_MISSING' }
$actualBackupHash = (Get-FileHash -LiteralPath $ValidatedBackup -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualBackupHash -cne $ValidatedBackupSha256.ToLowerInvariant()) { throw 'REHEARSAL_BACKUP_HASH_MISMATCH' }
$backupIdentity = Read-RequiredJson -Path $BackupIdentityPath -MissingCode 'REHEARSAL_BACKUP_IDENTITY_MISSING'
$manifest = Read-RequiredJson -Path $ManifestPath -MissingCode 'RELEASE_MANIFEST_MISSING'
if ([string]$backupIdentity.backup_sha256 -notmatch '^[a-fA-F0-9]{64}$' -or ([string]$backupIdentity.backup_sha256).ToLowerInvariant() -cne $actualBackupHash -or [string]$backupIdentity.rehearsal_database -cne $RehearsalDatabase) { throw 'REHEARSAL_BACKUP_IDENTITY_MISMATCH' }
if ($manifest.release_candidate -cne $ReleaseCandidate) { throw 'RELEASE_MANIFEST_CANDIDATE_MISMATCH' }

$root = [IO.Path]::GetFullPath($RehearsalRoot)
$pointerPath = Join-Path $root 'current-release.json'
$previousPointerPath = Join-Path $root 'previous-release.json'
$restoreScript = [IO.Path]::GetFullPath($DatabaseRestoreScript)
if (-not $restoreScript.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'DATABASE_RESTORE_SCRIPT_OUTSIDE_REHEARSAL_ROOT' }
if (-not (Test-Path -LiteralPath $restoreScript -PathType Leaf)) { throw 'DATABASE_RESTORE_SCRIPT_MISSING' }
if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) { throw 'ISOLATED_RELEASE_POINTER_MISSING' }
if (-not (Test-Path -LiteralPath $previousPointerPath -PathType Leaf)) { throw 'ISOLATED_PREVIOUS_RELEASE_POINTER_MISSING' }
$priorPointer = Read-RequiredJson -Path $previousPointerPath -MissingCode 'ISOLATED_PREVIOUS_RELEASE_POINTER_MISSING'
if ($priorPointer.release_candidate -notmatch '^[a-f0-9]{40}$') { throw 'ISOLATED_PREVIOUS_RELEASE_POINTER_INVALID' }
if (-not (Test-Path -LiteralPath $priorPointer.release_directory -PathType Container)) { throw 'ISOLATED_PREVIOUS_RELEASE_MISSING' }
if ($null -eq $priorPointer.PSObject.Properties['archive_sha256'] -or [string]$priorPointer.archive_sha256 -notmatch '^[a-fA-F0-9]{64}$' -or ([string]$priorPointer.archive_sha256).ToLowerInvariant() -cne $ExpectedPreviousArchiveSha256.ToLowerInvariant()) { throw 'ISOLATED_PREVIOUS_RELEASE_HASH_MISMATCH' }
if ($PSCmdlet.ShouldProcess($pointerPath, 'restore isolated current-release pointer from validated prior pointer')) {
    & $restoreScript -RehearsalDatabase $RehearsalDatabase -ValidatedBackup $ValidatedBackup -ReleaseCandidate $ReleaseCandidate
    if (-not $?) { throw 'ISOLATED_DATABASE_RESTORE_FAILED' }
    Copy-Item -LiteralPath $previousPointerPath -Destination $pointerPath -Force -ErrorAction Stop
}
[ordered]@{ status = 'PASS'; mode = 'Rehearsal'; operation = 'isolated_database_restored_and_release_pointer_restored'; release_candidate = $ReleaseCandidate; restored_release_candidate = $priorPointer.release_candidate; rehearsal_database = $RehearsalDatabase; backup_sha256 = $actualBackupHash; database_restore_required = $true; database_restore_executed = (-not $WhatIfPreference); current_pointer = $pointerPath; mutation_performed = (-not $WhatIfPreference); production_or_staging_mutated = $false } | ConvertTo-Json
