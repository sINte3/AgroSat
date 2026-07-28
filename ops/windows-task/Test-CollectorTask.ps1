[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigurationPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "Invoke-Collector.ps1") `
    -ConfigurationPath $ConfigurationPath `
    -Mode "dry-run"
exit $LASTEXITCODE
