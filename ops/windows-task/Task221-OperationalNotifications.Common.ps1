Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-Task221OperationalNotificationsConfiguration {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigurationPath,
        [switch]$RequireResolved
    )

    if (-not [IO.Path]::IsPathRooted($ConfigurationPath)) {
        throw "ConfigurationPath must be absolute."
    }
    $resolved = (Resolve-Path -LiteralPath $ConfigurationPath -ErrorAction Stop).Path
    $configuration = Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json
    foreach ($property in @(
        "task_name", "execution_identity", "execution_sid",
        "expected_windows_timezone_id", "release_commit",
        "immutable_release_root", "release_manifest_sha256",
        "working_directory", "python_executable", "runtime_env_file",
        "runner_script", "schedule", "reconciliation"
    )) {
        if (-not $configuration.PSObject.Properties.Name.Contains($property)) {
            throw "Configuration is missing required property: $property"
        }
    }
    if ($configuration.task_name -cne '\AgroSat_PROGRAM_R3_OperationalNotifications') {
        throw "Only the canonical TASK_221 notification task is supported."
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
    if ($configuration.schedule.multiple_instances -cne "IgnoreNew") {
        throw "Only the IgnoreNew single-instance policy is supported."
    }
    $bounds = @{
        "schedule.interval_minutes" = @([int]$configuration.schedule.interval_minutes, 5, 1440)
        "schedule.restart_count" = @([int]$configuration.schedule.restart_count, 0, 10)
        "schedule.restart_interval_minutes" = @([int]$configuration.schedule.restart_interval_minutes, 1, 1440)
        "schedule.execution_time_limit_minutes" = @([int]$configuration.schedule.execution_time_limit_minutes, 1, 60)
        "reconciliation.limit" = @([int]$configuration.reconciliation.limit, 1, 500)
    }
    foreach ($entry in $bounds.GetEnumerator()) {
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
            "working_directory", "immutable_release_root", "python_executable",
            "runtime_env_file", "runner_script"
        )) {
            $path = [string]$configuration.$pathProperty
            if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path)) {
                throw "$pathProperty must be an existing absolute path."
            }
        }
        $expectedRelease = Join-Path `
            ([string]$configuration.immutable_release_root) `
            ([string]$configuration.release_commit)
        if ((Resolve-Path -LiteralPath $configuration.working_directory).Path -cne `
            (Resolve-Path -LiteralPath $expectedRelease).Path) {
            throw "working_directory must be the exact immutable release directory."
        }
        $outputDirectory = [IO.Path]::GetFullPath([string]$configuration.reconciliation.output_directory)
        if (-not [IO.Path]::IsPathRooted($outputDirectory)) {
            throw "reconciliation.output_directory must be absolute."
        }
        $releasePath = (Resolve-Path -LiteralPath $expectedRelease).Path
        if ($outputDirectory.StartsWith($releasePath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Reconciliation runtime output must remain outside the immutable release."
        }
        if ((Get-TimeZone).Id -cne [string]$configuration.expected_windows_timezone_id) {
            throw "Windows timezone does not match expected_windows_timezone_id."
        }
    }
    return $configuration
}

function Split-Task221OperationalNotificationsTaskName {
    param([Parameter(Mandatory = $true)][string]$TaskName)
    $lastSeparator = $TaskName.LastIndexOf("\")
    return [pscustomobject]@{
        TaskPath = $TaskName.Substring(0, $lastSeparator + 1)
        TaskName = $TaskName.Substring($lastSeparator + 1)
    }
}
