Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-Task209CollectorConfiguration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConfigurationPath,
        [switch]$RequireResolved
    )

    if (-not [IO.Path]::IsPathRooted($ConfigurationPath)) {
        throw "ConfigurationPath must be absolute."
    }
    $resolved = (Resolve-Path -LiteralPath $ConfigurationPath -ErrorAction Stop).Path
    $configuration = Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json

    $requiredProperties = @(
        "task_name",
        "execution_identity",
        "expected_windows_timezone_id",
        "working_directory",
        "python_executable",
        "runtime_env_file",
        "runner_script",
        "schedule",
        "collector"
    )
    foreach ($property in $requiredProperties) {
        if (-not $configuration.PSObject.Properties.Name.Contains($property)) {
            throw "Configuration is missing required property: $property"
        }
    }

    if ($configuration.task_name -notmatch '^\\(?:[^\\]+\\)*[^\\]+$') {
        throw "task_name must use an absolute Task Scheduler path."
    }
    if ($configuration.schedule.multiple_instances -ne "IgnoreNew") {
        throw "Only the IgnoreNew single-instance policy is supported."
    }

    $boundedIntegers = @{
        "collector.batch_size" = @([int]$configuration.collector.batch_size, 1, 100)
        "collector.lookback_days" = @([int]$configuration.collector.lookback_days, 1, 30)
        "collector.max_attempts" = @([int]$configuration.collector.max_attempts, 1, 5)
        "collector.retry_base_seconds" = @([int]$configuration.collector.retry_base_seconds, 1, 300)
        "collector.field_timeout_seconds" = @([int]$configuration.collector.field_timeout_seconds, 1, 3600)
        "collector.cycle_timeout_seconds" = @([int]$configuration.collector.cycle_timeout_seconds, 1, 21600)
        "schedule.restart_count" = @([int]$configuration.schedule.restart_count, 0, 10)
        "schedule.restart_interval_minutes" = @([int]$configuration.schedule.restart_interval_minutes, 1, 1440)
        "schedule.execution_time_limit_hours" = @([int]$configuration.schedule.execution_time_limit_hours, 1, 24)
    }
    foreach ($entry in $boundedIntegers.GetEnumerator()) {
        $value, $minimum, $maximum = $entry.Value
        if ($value -lt $minimum -or $value -gt $maximum) {
            throw "$($entry.Key) must be between $minimum and $maximum."
        }
    }

    if ($RequireResolved) {
        $serialized = $configuration | ConvertTo-Json -Depth 8
        if ($serialized.Contains("<") -or $serialized.Contains(">")) {
            throw "Replace every configuration placeholder before apply."
        }
        foreach ($pathProperty in @(
            "working_directory",
            "python_executable",
            "runtime_env_file",
            "runner_script"
        )) {
            $path = [string]$configuration.$pathProperty
            if (-not [IO.Path]::IsPathRooted($path)) {
                throw "$pathProperty must be absolute."
            }
            if (-not (Test-Path -LiteralPath $path)) {
                throw "$pathProperty does not exist."
            }
        }
        if ((Get-TimeZone).Id -ne $configuration.expected_windows_timezone_id) {
            throw "Windows timezone does not match expected_windows_timezone_id."
        }
    }

    return $configuration
}

function Split-Task209TaskName {
    param([Parameter(Mandatory = $true)][string]$TaskName)

    $lastSeparator = $TaskName.LastIndexOf("\")
    return [pscustomobject]@{
        TaskPath = $TaskName.Substring(0, $lastSeparator + 1)
        TaskName = $TaskName.Substring($lastSeparator + 1)
    }
}
