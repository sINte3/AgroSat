[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param(
    [ValidateSet('Rehearsal','Production')][string]$Mode = 'Rehearsal',
    [string]$AuthorizationPath = '',
    [Parameter(Mandatory)][string]$RehearsalRoot,
    [Parameter(Mandatory)][string]$RehearsalDatabase,
    [Parameter(Mandatory)][string]$ValidatedBackup,
    [Parameter(Mandatory)][string]$ValidatedBackupSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$preflight = Join-Path $PSScriptRoot 'Test-AgroSatProductionPreflight.ps1'
& $preflight -Mode $Mode -AuthorizationPath $AuthorizationPath -RehearsalRoot $RehearsalRoot -RehearsalDatabase $RehearsalDatabase | Out-Null
if ($Mode -eq 'Production') { throw 'PRODUCTION_ROLLBACK_FAIL_CLOSED' }
if (-not (Test-Path -LiteralPath $ValidatedBackup -PathType Leaf)) { throw 'REHEARSAL_BACKUP_MISSING' }
if ((Get-FileHash -LiteralPath $ValidatedBackup -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ValidatedBackupSha256.ToLowerInvariant()) { throw 'REHEARSAL_BACKUP_HASH_MISMATCH' }
[ordered]@{ status='PASS'; mode='Rehearsal'; operation='isolated_rollback_validated'; release_target='40e8e379d9d29cb4bfb8afebdd9c489c19756fac'; rehearsal_database=$RehearsalDatabase; mutation_performed=$false } | ConvertTo-Json
