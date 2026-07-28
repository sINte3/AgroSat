[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigurationPath,
    [ValidateSet("dry-run", "diagnostic", "apply")]
    [string]$Mode = "dry-run"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Task209-CollectorTask.Common.ps1")

$configuration = Get-Task209CollectorConfiguration `
    -ConfigurationPath $ConfigurationPath `
    -RequireResolved

foreach ($directory in @(
    $configuration.collector.output_directory,
    $configuration.collector.state_directory,
    $configuration.collector.lock_directory
)) {
    if (-not [IO.Path]::IsPathRooted([string]$directory)) {
        throw "Collector directories must be absolute."
    }
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$collectorScript = Join-Path `
    $configuration.working_directory `
    "backend\scripts\collect_satellite.py"
if (-not (Test-Path -LiteralPath $collectorScript -PathType Leaf)) {
    throw "Canonical collector script is unavailable."
}

$modeArgument = switch ($Mode) {
    "dry-run" { "--dry-run" }
    "diagnostic" { "--diagnostic" }
    "apply" { "--apply" }
}

$arguments = @(
    $collectorScript,
    $modeArgument,
    "--all-active-fields",
    "--indices", [string]$configuration.collector.indices,
    "--batch-size", [string]$configuration.collector.batch_size,
    "--lookback-days", [string]$configuration.collector.lookback_days,
    "--max-attempts", [string]$configuration.collector.max_attempts,
    "--retry-base-seconds", [string]$configuration.collector.retry_base_seconds,
    "--field-timeout-seconds", [string]$configuration.collector.field_timeout_seconds,
    "--cycle-timeout-seconds", [string]$configuration.collector.cycle_timeout_seconds,
    "--output-dir", [string]$configuration.collector.output_directory,
    "--state-dir", [string]$configuration.collector.state_directory,
    "--lock-dir", [string]$configuration.collector.lock_directory
)

$previousRuntimeFile = $env:AGROSAT_RUNTIME_ENV_FILE
try {
    $env:AGROSAT_RUNTIME_ENV_FILE = [string]$configuration.runtime_env_file
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
}
