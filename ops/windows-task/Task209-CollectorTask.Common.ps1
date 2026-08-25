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
        "execution_sid",
        "expected_windows_timezone_id",
        "release_commit",
        "immutable_release_root",
        "release_manifest_sha256",
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
    if ($configuration.task_name -cne '\AgroSat_PROGRAM_R3_SentinelCycle') {
        throw "TASK_219 supports only the canonical scheduler identity."
    }
    if ([string]$configuration.execution_sid -notmatch '^S-1-[0-9-]+$') {
        throw "execution_sid must be an explicit Windows SID."
    }
    if ([string]$configuration.release_commit -notmatch '^[0-9a-f]{40}$') {
        throw "release_commit must be an exact lowercase Git commit."
    }
    if ([string]$configuration.release_manifest_sha256 -notmatch '^[0-9a-f]{64}$') {
        throw "release_manifest_sha256 must be SHA-256."
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
    if (@($configuration.schedule.daily_at_local_times).Count -notin @(1, 2)) {
        throw "daily_at_local_times must contain one or two bounded triggers."
    }
    foreach ($value in @($configuration.schedule.daily_at_local_times)) {
        $parsed = [TimeSpan]::Zero
        if (-not [TimeSpan]::TryParse([string]$value, [ref]$parsed) -or $parsed -lt [TimeSpan]::Zero -or $parsed -ge [TimeSpan]::FromDays(1)) {
            throw "daily_at_local_times contains an invalid local time."
        }
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
            "immutable_release_root",
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
        $expectedRelease = Join-Path ([string]$configuration.immutable_release_root) ([string]$configuration.release_commit)
        if ((Resolve-Path -LiteralPath $configuration.working_directory).Path -cne (Resolve-Path -LiteralPath $expectedRelease).Path) {
            throw "working_directory must be the exact immutable release directory."
        }
        foreach ($directory in @($configuration.collector.output_directory,$configuration.collector.state_directory,$configuration.collector.lock_directory)) {
            if (-not [IO.Path]::IsPathRooted([string]$directory)) { throw "Collector runtime directories must be absolute." }
            $full = [IO.Path]::GetFullPath([string]$directory)
            if ($full.StartsWith((Resolve-Path -LiteralPath $expectedRelease).Path + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
                throw "Collector runtime directories must remain outside the immutable release."
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
