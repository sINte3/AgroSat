Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# The canonical production Sentinel task and its accepted schedule contract
# (TASK_227/TASK_230): exactly one daily trigger at 06:00:00 local time, no
# 18:00 run, SYSTEM, IgnoreNew, StartWhenAvailable, and the current bounded
# execution limits. Changing any value here is an explicit production change.
$script:AgroSatSentinelProductionTaskName = '\AgroSat_PROGRAM_R3_SentinelCycle'
$script:AgroSatSentinelProductionDailyTime = '06:00:00'
$script:AgroSatSentinelProductionContract = [ordered]@{
    execution_sid = 'S-1-5-18'
    multiple_instances = 'IgnoreNew'
    start_when_available = $true
    execution_time_limit_hours = 6
    restart_count = 3
    restart_interval_minutes = 15
}
# Rehearsal and test tasks are named explicitly per task and can never be
# mistaken for, or registered as, the production identity.
$script:AgroSatRehearsalTaskNamePattern = '^\\AgroSat_TASK[0-9]{3}_[A-Za-z0-9_]{1,64}$'

function Test-AgroSatSentinelProductionIdentity {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$TaskName)
    return $TaskName -ceq $script:AgroSatSentinelProductionTaskName
}

function Assert-AgroSatSentinelProductionSchedule {
    param([Parameter(Mandatory = $true)]$Configuration)

    $times = @($Configuration.schedule.daily_at_local_times)
    if ($times.Count -ne 1) {
        throw "SENTINEL_PRODUCTION_TRIGGER_COUNT_REJECTED: the production Sentinel cycle has exactly one daily trigger at $($script:AgroSatSentinelProductionDailyTime)."
    }
    $parsed = [TimeSpan]::Zero
    if (-not [TimeSpan]::TryParseExact([string]$times[0], 'hh\:mm\:ss', [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed) -or
        $parsed -ne [TimeSpan]::Parse($script:AgroSatSentinelProductionDailyTime)) {
        throw "SENTINEL_PRODUCTION_TRIGGER_TIME_REJECTED: the production Sentinel trigger is $($script:AgroSatSentinelProductionDailyTime) local time only."
    }
    $contract = $script:AgroSatSentinelProductionContract
    if ([string]$Configuration.execution_sid -cne $contract.execution_sid) {
        throw "SENTINEL_PRODUCTION_IDENTITY_REJECTED: the production Sentinel cycle runs as SYSTEM."
    }
    if ([string]$Configuration.schedule.multiple_instances -cne $contract.multiple_instances) {
        throw "SENTINEL_PRODUCTION_INSTANCE_POLICY_REJECTED"
    }
    if ($Configuration.schedule.start_when_available -isnot [bool] -or $Configuration.schedule.start_when_available -ne $contract.start_when_available) {
        throw "SENTINEL_PRODUCTION_START_WHEN_AVAILABLE_REJECTED"
    }
    foreach ($name in @('execution_time_limit_hours', 'restart_count', 'restart_interval_minutes')) {
        if ([int]$Configuration.schedule.$name -ne $contract[$name]) {
            throw "SENTINEL_PRODUCTION_LIMIT_REJECTED:$name"
        }
    }
}

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
    $productionIdentity = Test-AgroSatSentinelProductionIdentity -TaskName ([string]$configuration.task_name)
    if (-not $productionIdentity -and [string]$configuration.task_name -cnotmatch $script:AgroSatRehearsalTaskNamePattern) {
        throw "Only the canonical production Sentinel identity or an explicit \AgroSat_TASKnnn_* rehearsal identity is supported."
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
    if ($productionIdentity) {
        # The production identity always carries the exact production schedule;
        # the bounded generic rules above are only for rehearsal identities.
        Assert-AgroSatSentinelProductionSchedule -Configuration $configuration
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

function ConvertTo-AgroSatPrincipalSid {
    # Task Scheduler reports SYSTEM by its localized account name; compare SIDs.
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$UserId)

    if ($UserId -match '^S-1-[0-9-]+$') { return $UserId }
    try {
        return (New-Object System.Security.Principal.NTAccount($UserId)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    catch {
        return "unresolved"
    }
}

function Get-AgroSatSentinelRegisteredContractViolations {
    # Compares a registered Sentinel task with the production contract. Read only.
    param([Parameter(Mandatory = $true)]$RegisteredTask)

    $violations = New-Object System.Collections.Generic.List[string]
    $triggers = @($RegisteredTask.Triggers)
    if ($triggers.Count -ne 1) {
        $violations.Add("trigger_count:$($triggers.Count)")
    }
    foreach ($trigger in $triggers) {
        if ($trigger.CimClass.CimClassName -ne 'MSFT_TaskDailyTrigger') {
            $violations.Add("trigger_kind:$($trigger.CimClass.CimClassName)")
            continue
        }
        if ([int]$trigger.DaysInterval -ne 1) { $violations.Add("days_interval:$($trigger.DaysInterval)") }
        $boundary = [DateTime]::Parse([string]$trigger.StartBoundary, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
        $local = if ($boundary.Kind -eq [DateTimeKind]::Unspecified) { $boundary } else { $boundary.ToLocalTime() }
        if ($local.TimeOfDay -ne [TimeSpan]::Parse($script:AgroSatSentinelProductionDailyTime)) {
            $violations.Add("trigger_time:$($local.TimeOfDay)")
        }
        if (-not $trigger.Enabled) { $violations.Add("trigger_disabled") }
    }
    $contract = $script:AgroSatSentinelProductionContract
    $principalSid = ConvertTo-AgroSatPrincipalSid -UserId ([string]$RegisteredTask.Principal.UserId)
    if ($principalSid -cne $contract.execution_sid) {
        $violations.Add("principal:$principalSid")
    }
    $settings = $RegisteredTask.Settings
    if ([string]$settings.MultipleInstances -ne $contract.multiple_instances) { $violations.Add("multiple_instances:$($settings.MultipleInstances)") }
    if (-not $settings.StartWhenAvailable) { $violations.Add("start_when_available:false") }
    if ([string]$settings.ExecutionTimeLimit -ne ("PT{0}H" -f $contract.execution_time_limit_hours)) { $violations.Add("execution_time_limit:$($settings.ExecutionTimeLimit)") }
    if ([int]$settings.RestartCount -ne $contract.restart_count) { $violations.Add("restart_count:$($settings.RestartCount)") }
    if ([string]$settings.RestartInterval -ne ("PT{0}M" -f $contract.restart_interval_minutes)) { $violations.Add("restart_interval:$($settings.RestartInterval)") }
    return ,$violations.ToArray()
}
