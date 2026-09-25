[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)][string]$PolicyPath,
    [Parameter(Mandatory = $true)][string]$ReleaseDirectory,
    [Parameter(Mandatory = $true)][string]$ControlRoot,
    [switch]$Apply
)

# Registers the recurring database backup as an external Windows Scheduled Task
# (never a scheduler inside FastAPI). Everything comes from the explicit backup
# policy: no backup time, identity or limit has a default here. The task runs
# the immutable release's control plane:
#   python -B <release>\ops\release\Invoke-AgroSatControlPlane.py backup scheduled
# with IgnoreNew, a bounded execution limit and bounded retries, and is
# installed DISABLED. Enabling it is a separate, reviewed step. An existing
# task is never overwritten.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-AbsolutePath([string]$Value, [string]$Name) {
    if (-not [IO.Path]::IsPathRooted($Value)) { throw "$Name must be an absolute path." }
}

Assert-AbsolutePath $PolicyPath "PolicyPath"
Assert-AbsolutePath $ReleaseDirectory "ReleaseDirectory"
Assert-AbsolutePath $ControlRoot "ControlRoot"
$policy = Get-Content -LiteralPath $PolicyPath -Raw | ConvertFrom-Json
if ($policy.kind -cne "agrosat_database_backup_policy" -or $policy.schema_version -ne 1) { throw "BACKUP_POLICY_SCHEMA_REJECTED" }
if ($policy.example_only -ne $false) { throw "BACKUP_POLICY_IS_EXAMPLE: an example policy is never an operating policy." }
$serialized = $policy | ConvertTo-Json -Depth 8
if ($serialized.Contains("<") -or $serialized.Contains(">")) { throw "BACKUP_POLICY_PLACEHOLDER: replace every placeholder." }
$schedule = $policy.schedule
if ($null -eq $schedule) { throw "BACKUP_SCHEDULE_REQUIRED: the policy has no explicit schedule." }
if ([string]$schedule.daily_at_local_time -notmatch '^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$') {
    throw "BACKUP_SCHEDULE_TIME_REJECTED: an explicit HH:MM:SS local time is required."
}
if ([string]$schedule.execution_sid -notmatch '^S-1-[0-9-]+$') { throw "BACKUP_SCHEDULE_IDENTITY_REJECTED" }
$limit = [int]$schedule.execution_time_limit_minutes
$restarts = [int]$schedule.restart_count
$restartInterval = [int]$schedule.restart_interval_minutes
if ($limit -lt 5 -or $limit -gt 240) { throw "BACKUP_SCHEDULE_LIMIT_REJECTED: execution_time_limit_minutes" }
if ($restarts -lt 0 -or $restarts -gt 3) { throw "BACKUP_SCHEDULE_LIMIT_REJECTED: restart_count" }
if ($restartInterval -lt 1 -or $restartInterval -gt 60) { throw "BACKUP_SCHEDULE_LIMIT_REJECTED: restart_interval_minutes" }
$taskName = [string]$schedule.task_name
$production = [string]$policy.source_classification -ceq "production"
if ($production -ne ($taskName -ceq '\AgroSat_PROGRAM_R3_DatabaseBackup')) {
    throw "BACKUP_SCHEDULE_TASK_REJECTED: the canonical task name belongs to the production policy only."
}
if (-not $production -and $taskName -cnotmatch '^\\AgroSat_TASK[0-9]{3}_[A-Za-z0-9_]{1,64}$') {
    throw "BACKUP_SCHEDULE_TASK_REJECTED: rehearsal backup tasks are \AgroSat_TASKnnn_*."
}

$release = (Resolve-Path -LiteralPath $ReleaseDirectory).Path
$manifest = Get-Content -LiteralPath (Join-Path $release "release-manifest.json") -Raw | ConvertFrom-Json
if ([string]$manifest.git_sha -cne (Split-Path -Leaf $release)) { throw "RELEASE_IDENTITY_REJECTED" }
$python = Join-Path $release "backend\venv\Scripts\python.exe"
$controlPlane = Join-Path $release "ops\release\Invoke-AgroSatControlPlane.py"
foreach ($path in @($python, $controlPlane)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "RELEASE_ASSET_MISSING: $([IO.Path]::GetFileName($path))" }
}
$arguments = ('-B "{0}" backup scheduled --policy "{1}" --control-root "{2}"' -f $controlPlane, $PolicyPath, $ControlRoot)
$leaf = $taskName.Substring(1)

if (-not $Apply) {
    [pscustomobject]@{
        action = "preview_install"
        task_name = $taskName
        daily_at_local_time = $schedule.daily_at_local_time
        execution_sid = $schedule.execution_sid
        execution_time_limit_minutes = $limit
        restart_count = $restarts
        restart_interval_minutes = $restartInterval
        multiple_instances = "IgnoreNew"
        execute = $python
        arguments = $arguments
        working_directory = $release
        release_commit = $manifest.git_sha
        state_after_install = "Disabled"
        applied = $false
    } | ConvertTo-Json
    exit 0
}

if ($null -ne (Get-ScheduledTask -TaskPath '\' -TaskName $leaf -ErrorAction SilentlyContinue)) {
    throw "Scheduled Task already exists; inspect it or remove it explicitly first."
}
$time = [TimeSpan]::Parse([string]$schedule.daily_at_local_time)
$firstRun = (Get-Date).Date.Add($time)
if ($firstRun -le (Get-Date)) { $firstRun = $firstRun.AddDays(1) }
$trigger = New-ScheduledTaskTrigger -Daily -At $firstRun
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $release
$principal = New-ScheduledTaskPrincipal -UserId ([string]$schedule.execution_sid) -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $limit) `
    -RestartCount $restarts `
    -RestartInterval (New-TimeSpan -Minutes $restartInterval) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if ($PSCmdlet.ShouldProcess($taskName, "Register disabled database backup task")) {
    Register-ScheduledTask -TaskPath '\' -TaskName $leaf -Description "AgroSat database backup (custom-format, validated, retained by explicit policy)" `
        -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    Disable-ScheduledTask -TaskPath '\' -TaskName $leaf | Out-Null
}
[pscustomobject]@{
    action = "installed_disabled"
    task_name = $taskName
    state = "Disabled"
    daily_at_local_time = $schedule.daily_at_local_time
    release_commit = $manifest.git_sha
} | ConvertTo-Json
