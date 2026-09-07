[CmdletBinding()]
param(
    [ValidateSet('Rehearsal','Production')][string]$Mode = 'Rehearsal',
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ReleaseCandidate,
    [string]$RehearsalRoot = '',
    [string]$RehearsalDatabase = '',
    [string]$AuthorizationPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Mode -eq 'Production') {
    if ([string]::IsNullOrWhiteSpace($AuthorizationPath) -or -not (Test-Path -LiteralPath $AuthorizationPath -PathType Leaf)) {
        throw 'PHASE3_AUTHORIZATION_MISSING'
    }
    throw 'PRODUCTION_MODE_REQUIRES_SEPARATE_PHASE3_AUTHORIZATION_AND_EXECUTION'
}

if ([string]::IsNullOrWhiteSpace($RehearsalRoot) -or [string]::IsNullOrWhiteSpace($RehearsalDatabase)) { throw 'REHEARSAL_TARGET_REQUIRED' }
$root = [IO.Path]::GetFullPath($RehearsalRoot)
$task218Root = 'C:\AgroSat_backups\PROGRAM_R3_MACROSTAGE_D_PRODUCTION_RELEASE\RUNS\'
$task219Root = 'C:\AgroSat_backups\PROGRAM_R3_MACROSTAGE_E_AUTONOMOUS_MONITORING\RUNS\'
$task220Root = 'C:\AgroSat_backups\PROGRAM_R3_MACROSTAGE_F_CLOSED_LOOP_AGRONOMY\RUNS\'
$legacyRoot = (
    $root.StartsWith('C:\AgroSat_rehearsal\PROGRAM_R3_MEGA_RELEASE_REPAIR\RUNS\', [StringComparison]::OrdinalIgnoreCase) -or
    $root.StartsWith('C:\AgroSat_backups\PROGRAM_R3_FAST_TRACK_RELEASE_CANDIDATE\RUNS\', [StringComparison]::OrdinalIgnoreCase)
)
$task218 = $root.StartsWith($task218Root, [StringComparison]::OrdinalIgnoreCase)
$task219 = $root.StartsWith($task219Root, [StringComparison]::OrdinalIgnoreCase)
$task220 = $root.StartsWith($task220Root, [StringComparison]::OrdinalIgnoreCase)
if (-not ($legacyRoot -or $task218 -or $task219 -or $task220)) { throw 'REHEARSAL_ROOT_GUARD_FAILED' }
if ($root -eq 'C:\AgroSat_rehearsal\PROGRAM_R3_MEGA_RELEASE_REPAIR\RUNS') { throw 'REHEARSAL_ROOT_MUST_BE_RUN_DIRECTORY' }
if ($task220) {
    if ($RehearsalDatabase -notmatch '^agrosat_r3_task220_[a-z0-9_]+$') { throw 'REHEARSAL_DATABASE_GUARD_FAILED' }
} elseif ($task219) {
    if ($RehearsalDatabase -notmatch '^agrosat_r3_task219_[a-z0-9_]+$') { throw 'REHEARSAL_DATABASE_GUARD_FAILED' }
} elseif ($task218) {
    if ($RehearsalDatabase -notmatch '^agrosat_r3_task218_[a-z0-9_]+$') { throw 'REHEARSAL_DATABASE_GUARD_FAILED' }
} elseif ($RehearsalDatabase -notmatch '^agrosat_r3_rc_[a-z0-9_]+$') {
    throw 'REHEARSAL_DATABASE_GUARD_FAILED'
}
[ordered]@{ status='PASS'; mode='Rehearsal'; release_candidate=$ReleaseCandidate; rehearsal_root=$root; rehearsal_database=$RehearsalDatabase; mutation_performed=$false } | ConvertTo-Json
