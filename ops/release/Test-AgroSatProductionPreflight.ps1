[CmdletBinding()]
param(
    [ValidateSet('Rehearsal','Production')][string]$Mode = 'Rehearsal',
    [string]$ReleaseTarget = '40e8e379d9d29cb4bfb8afebdd9c489c19756fac',
    [string]$RehearsalRoot = '',
    [string]$RehearsalDatabase = '',
    [string]$AuthorizationPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$expected = '40e8e379d9d29cb4bfb8afebdd9c489c19756fac'
if ($ReleaseTarget -cne $expected) { throw 'RELEASE_TARGET_MISMATCH' }

if ($Mode -eq 'Production') {
    if ([string]::IsNullOrWhiteSpace($AuthorizationPath) -or -not (Test-Path -LiteralPath $AuthorizationPath -PathType Leaf)) {
        throw 'PHASE3_AUTHORIZATION_MISSING'
    }
    $authorization = Get-Content -LiteralPath $AuthorizationPath -Raw | ConvertFrom-Json
    foreach ($field in @('release_target','production_deployment_authorized','migration_authorized')) {
        if ($null -eq $authorization.PSObject.Properties[$field]) { throw "PHASE3_AUTHORIZATION_FIELD_MISSING:$field" }
    }
    if ($authorization.release_target -cne $expected -or -not $authorization.production_deployment_authorized -or -not $authorization.migration_authorized) {
        throw 'PHASE3_AUTHORIZATION_INVALID'
    }
    throw 'PRODUCTION_MODE_REQUIRES_SEPARATE_PHASE3_AUTHORIZATION_AND_EXECUTION'
}

if ([string]::IsNullOrWhiteSpace($RehearsalRoot) -or [string]::IsNullOrWhiteSpace($RehearsalDatabase)) { throw 'REHEARSAL_TARGET_REQUIRED' }
$root = [IO.Path]::GetFullPath($RehearsalRoot)
if (-not $root.StartsWith('C:\AgroSat_rehearsal\PROGRAM_R3_MEGA_RELEASE_REPAIR\RUNS\', [StringComparison]::OrdinalIgnoreCase)) { throw 'REHEARSAL_ROOT_GUARD_FAILED' }
if ($RehearsalDatabase -notmatch '^agrosat_r3_fix_[a-z0-9_]+$') { throw 'REHEARSAL_DATABASE_GUARD_FAILED' }
[ordered]@{ status='PASS'; mode='Rehearsal'; release_target=$expected; rehearsal_root=$root; rehearsal_database=$RehearsalDatabase; mutation_performed=$false } | ConvertTo-Json
