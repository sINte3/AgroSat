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
if (-not $root.StartsWith('C:\AgroSat_rehearsal\PROGRAM_R3_MEGA_RELEASE_REPAIR\RUNS\', [StringComparison]::OrdinalIgnoreCase)) { throw 'REHEARSAL_ROOT_GUARD_FAILED' }
if ($root -eq 'C:\AgroSat_rehearsal\PROGRAM_R3_MEGA_RELEASE_REPAIR\RUNS') { throw 'REHEARSAL_ROOT_MUST_BE_RUN_DIRECTORY' }
if ($RehearsalDatabase -notmatch '^agrosat_r3_fix_[a-z0-9_]+$') { throw 'REHEARSAL_DATABASE_GUARD_FAILED' }
[ordered]@{ status='PASS'; mode='Rehearsal'; release_candidate=$ReleaseCandidate; rehearsal_root=$root; rehearsal_database=$RehearsalDatabase; mutation_performed=$false } | ConvertTo-Json
