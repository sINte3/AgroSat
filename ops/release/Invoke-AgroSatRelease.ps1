[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param(
    [ValidateSet('Rehearsal','Production')][string]$Mode = 'Rehearsal',
    [string]$AuthorizationPath = '',
    [Parameter(Mandatory)][string]$RehearsalRoot,
    [Parameter(Mandatory)][string]$RehearsalDatabase,
    [Parameter(Mandatory)][string]$SourceArchive,
    [Parameter(Mandatory)][string]$SourceArchiveSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$preflight = Join-Path $PSScriptRoot 'Test-AgroSatProductionPreflight.ps1'
& $preflight -Mode $Mode -AuthorizationPath $AuthorizationPath -RehearsalRoot $RehearsalRoot -RehearsalDatabase $RehearsalDatabase | Out-Null
if ($Mode -eq 'Production') { throw 'PRODUCTION_RELEASE_FAIL_CLOSED' }
if (-not (Test-Path -LiteralPath $SourceArchive -PathType Leaf)) { throw 'SOURCE_ARCHIVE_MISSING' }
if ((Get-FileHash -LiteralPath $SourceArchive -Algorithm SHA256).Hash.ToLowerInvariant() -cne $SourceArchiveSha256.ToLowerInvariant()) { throw 'SOURCE_ARCHIVE_HASH_MISMATCH' }
$release = Join-Path ([IO.Path]::GetFullPath($RehearsalRoot)) 'release\40e8e379d9d29cb4bfb8afebdd9c489c19756fac'
if ($PSCmdlet.ShouldProcess($release, 'materialize isolated release archive')) {
    New-Item -ItemType Directory -Path $release -ErrorAction Stop | Out-Null
    Expand-Archive -LiteralPath $SourceArchive -DestinationPath $release -Force
}
[ordered]@{ status='PASS'; mode='Rehearsal'; operation='isolated_release_materialized'; release_target='40e8e379d9d29cb4bfb8afebdd9c489c19756fac'; release_directory=$release; mutation_performed=(-not $WhatIfPreference) } | ConvertTo-Json
