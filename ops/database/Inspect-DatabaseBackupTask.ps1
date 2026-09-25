[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$PolicyPath)

# Read-only: compares the registered backup task with its explicit policy.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$policy = Get-Content -LiteralPath $PolicyPath -Raw | ConvertFrom-Json
if ($null -eq $policy.schedule) { throw "BACKUP_SCHEDULE_REQUIRED" }
$taskName = [string]$policy.schedule.task_name
$leaf = $taskName.Substring(1)
$task = Get-ScheduledTask -TaskPath '\' -TaskName $leaf -ErrorAction SilentlyContinue
if ($null -eq $task) {
    [pscustomobject]@{ installed = $false; task_name = $taskName } | ConvertTo-Json
    exit 1
}
$info = Get-ScheduledTaskInfo -TaskPath '\' -TaskName $leaf
$violations = New-Object System.Collections.Generic.List[string]
$triggers = @($task.Triggers)
if ($triggers.Count -ne 1) { $violations.Add("trigger_count:$($triggers.Count)") }
foreach ($trigger in $triggers) {
    $boundary = [DateTime]::Parse([string]$trigger.StartBoundary, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
    $local = if ($boundary.Kind -eq [DateTimeKind]::Unspecified) { $boundary } else { $boundary.ToLocalTime() }
    if ($local.TimeOfDay -ne [TimeSpan]::Parse([string]$policy.schedule.daily_at_local_time)) { $violations.Add("trigger_time:$($local.TimeOfDay)") }
}
$userId = [string]$task.Principal.UserId
$sid = if ($userId -match '^S-1-[0-9-]+$') { $userId } else {
    try { (New-Object System.Security.Principal.NTAccount($userId)).Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { "unresolved" }
}
if ($sid -cne [string]$policy.schedule.execution_sid) { $violations.Add("principal:$sid") }
if ([string]$task.Settings.MultipleInstances -ne "IgnoreNew") { $violations.Add("multiple_instances:$($task.Settings.MultipleInstances)") }
if ([string]$task.Settings.ExecutionTimeLimit -ne ("PT{0}M" -f [int]$policy.schedule.execution_time_limit_minutes) -and
    [string]$task.Settings.ExecutionTimeLimit -ne ("PT{0}H" -f ([int]$policy.schedule.execution_time_limit_minutes / 60))) {
    $violations.Add("execution_time_limit:$($task.Settings.ExecutionTimeLimit)")
}
if ([int]$task.Settings.RestartCount -ne [int]$policy.schedule.restart_count) { $violations.Add("restart_count:$($task.Settings.RestartCount)") }
[pscustomobject]@{
    installed = $true
    task_name = $taskName
    state = [string]$task.State
    last_run_time = $info.LastRunTime.ToString("o")
    last_task_result = $info.LastTaskResult
    next_run_time = $info.NextRunTime.ToString("o")
    contract_violations = $violations.ToArray()
    contract_ok = ($violations.Count -eq 0)
} | ConvertTo-Json
if ($violations.Count -ne 0) { exit 2 }
