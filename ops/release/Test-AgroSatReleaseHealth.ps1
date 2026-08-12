[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BaseUrl,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ReleaseCandidate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$uri = [Uri]$BaseUrl
if ($uri.Scheme -ne 'http' -or $uri.Host -notin @('127.0.0.1','localhost','::1')) { throw 'HEALTH_TARGET_NOT_LOOPBACK' }
$live = Invoke-RestMethod -Uri "$BaseUrl/health/live" -TimeoutSec 10
$ready = Invoke-RestMethod -Uri "$BaseUrl/health/ready" -TimeoutSec 10
if ($live.release_revision -cne $ReleaseCandidate -or $ready.release_revision -cne $ReleaseCandidate) { throw 'HEALTH_RELEASE_MISMATCH' }
[ordered]@{ status='PASS'; base_url=$BaseUrl; release_candidate=$ReleaseCandidate; live=$live.status; ready=$ready.status } | ConvertTo-Json
