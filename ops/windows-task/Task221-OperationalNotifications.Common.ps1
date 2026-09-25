Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# The canonical production notification task and its accepted schedule
# contract (TASK_221/TASK_227/TASK_230): every 15 minutes, IgnoreNew, SYSTEM,
# a 10-minute execution limit and 3 restarts 5 minutes apart. A release or
# rebind can never change these; changing a value here is an explicit
# production change.
$script:AgroSatNotificationsProductionTaskName = '\AgroSat_PROGRAM_R3_OperationalNotifications'
$script:AgroSatNotificationsProductionContract = [ordered]@{
    interval_minutes = 15
    execution_sid = 'S-1-5-18'
    multiple_instances = 'IgnoreNew'
    start_when_available = $true
    execution_time_limit_minutes = 10
    restart_count = 3
    restart_interval_minutes = 5
}
$script:AgroSatNotificationsRehearsalTaskNamePattern = '^\\AgroSat_TASK[0-9]{3}_[A-Za-z0-9_]{1,64}$'

function Test-AgroSatNotificationsProductionIdentity {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$TaskName)
    return $TaskName -ceq $script:AgroSatNotificationsProductionTaskName
}

function Assert-AgroSatNotificationsProductionSchedule {
    param([Parameter(Mandatory = $true)]$Configuration)

    $contract = $script:AgroSatNotificationsProductionContract
    if ([string]$Configuration.execution_sid -cne $contract.execution_sid) {
        throw "NOTIFICATIONS_PRODUCTION_IDENTITY_REJECTED: the production notification task runs as SYSTEM."
    }
    if ([string]$Configuration.schedule.multiple_instances -cne $contract.multiple_instances) {
        throw "NOTIFICATIONS_PRODUCTION_INSTANCE_POLICY_REJECTED"
    }
    if ($Configuration.schedule.start_when_available -isnot [bool] -or $Configuration.schedule.start_when_available -ne $contract.start_when_available) {
        throw "NOTIFICATIONS_PRODUCTION_START_WHEN_AVAILABLE_REJECTED"
    }
    foreach ($name in @('interval_minutes', 'execution_time_limit_minutes', 'restart_count', 'restart_interval_minutes')) {
        if ([int]$Configuration.schedule.$name -ne $contract[$name]) {
            throw "NOTIFICATIONS_PRODUCTION_SCHEDULE_REJECTED:$name"
        }
    }
}

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
    $productionIdentity = Test-AgroSatNotificationsProductionIdentity -TaskName ([string]$configuration.task_name)
    if (-not $productionIdentity -and [string]$configuration.task_name -cnotmatch $script:AgroSatNotificationsRehearsalTaskNamePattern) {
        throw "Only the canonical TASK_221 notification task or an explicit \AgroSat_TASKnnn_* rehearsal identity is supported."
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
    if ($productionIdentity) {
        Assert-AgroSatNotificationsProductionSchedule -Configuration $configuration
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

function Get-AgroSatNotificationsRegisteredContractViolations {
    # Compares a registered notification task with the production contract. Read only.
    param([Parameter(Mandatory = $true)]$RegisteredTask)

    $contract = $script:AgroSatNotificationsProductionContract
    $violations = New-Object System.Collections.Generic.List[string]
    $triggers = @($RegisteredTask.Triggers)
    if ($triggers.Count -ne 1) {
        $violations.Add("trigger_count:$($triggers.Count)")
    }
    foreach ($trigger in $triggers) {
        $interval = if ($null -ne $trigger.Repetition) { [string]$trigger.Repetition.Interval } else { "" }
        if ($interval -ne ("PT{0}M" -f $contract.interval_minutes)) { $violations.Add("repetition_interval:$interval") }
        if (-not $trigger.Enabled) { $violations.Add("trigger_disabled") }
    }
    $userId = [string]$RegisteredTask.Principal.UserId
    $principalSid = if ($userId -match '^S-1-[0-9-]+$') { $userId } else {
        try { (New-Object System.Security.Principal.NTAccount($userId)).Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { "unresolved" }
    }
    if ($principalSid -cne $contract.execution_sid) { $violations.Add("principal:$principalSid") }
    $settings = $RegisteredTask.Settings
    if ([string]$settings.MultipleInstances -ne $contract.multiple_instances) { $violations.Add("multiple_instances:$($settings.MultipleInstances)") }
    if (-not $settings.StartWhenAvailable) { $violations.Add("start_when_available:false") }
    if ([string]$settings.ExecutionTimeLimit -ne ("PT{0}M" -f $contract.execution_time_limit_minutes)) { $violations.Add("execution_time_limit:$($settings.ExecutionTimeLimit)") }
    if ([int]$settings.RestartCount -ne $contract.restart_count) { $violations.Add("restart_count:$($settings.RestartCount)") }
    if ([string]$settings.RestartInterval -ne ("PT{0}M" -f $contract.restart_interval_minutes)) { $violations.Add("restart_interval:$($settings.RestartInterval)") }
    return ,$violations.ToArray()
}
