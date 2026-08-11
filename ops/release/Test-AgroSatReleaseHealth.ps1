[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BaseUrl,
    [string]$ReleaseTarget = '40e8e379d9d29cb4bfb8afebdd9c489c19756fac'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$uri = [Uri]$BaseUrl
if ($uri.Scheme -ne 'http' -or $uri.Host -notin @('127.0.0.1','localhost','::1')) { throw 'HEALTH_TARGET_NOT_LOOPBACK' }
$live = Invoke-RestMethod -Uri "$BaseUrl/health/live" -TimeoutSec 10
$ready = Invoke-RestMethod -Uri "$BaseUrl/health/ready" -TimeoutSec 10
if ($live.release_revision -cne $ReleaseTarget -or $ready.release_revision -cne $ReleaseTarget) { throw 'HEALTH_RELEASE_MISMATCH' }
[ordered]@{ status='PASS'; base_url=$BaseUrl; release_target=$ReleaseTarget; live=$live.status; ready=$ready.status } | ConvertTo-Json
