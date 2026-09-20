[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ConfigurationPath,
    [ValidateSet("dry-run", "apply")][string]$Mode = "dry-run"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Task221-OperationalNotifications.Common.ps1")

$configuration = Get-Task221OperationalNotificationsConfiguration `
    -ConfigurationPath $ConfigurationPath -RequireResolved
$manifestPath = Join-Path $configuration.working_directory "release-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Immutable release manifest is unavailable."
}
$manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($manifestHash -cne ([string]$configuration.release_manifest_sha256).ToLowerInvariant()) {
    throw "Immutable release manifest hash mismatch."
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ([string]$manifest.git_sha -cne [string]$configuration.release_commit) {
    throw "Immutable release manifest commit mismatch."
}

$script = Join-Path $configuration.working_directory "backend\scripts\reconcile_operational_notifications.py"
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    throw "Operational notification reconciler is unavailable."
}
$outputDirectory = [string]$configuration.reconciliation.output_directory
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$modeArgument = if ($Mode -eq "apply") { "--apply" } else { "--dry-run" }
$arguments = @(
    $script, $modeArgument,
    "--limit", [string]$configuration.reconciliation.limit,
    "--output-dir", $outputDirectory
)

$previousRuntimeFile = $env:AGROSAT_RUNTIME_ENV_FILE
$previousReleaseCommit = $env:AGROSAT_RELEASE_COMMIT
try {
    $env:AGROSAT_RUNTIME_ENV_FILE = [string]$configuration.runtime_env_file
    $env:AGROSAT_RELEASE_COMMIT = [string]$configuration.release_commit
    Push-Location -LiteralPath $configuration.working_directory
    try {
        & $configuration.python_executable @arguments
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:AGROSAT_RUNTIME_ENV_FILE = $previousRuntimeFile
    $env:AGROSAT_RELEASE_COMMIT = $previousReleaseCommit
}
