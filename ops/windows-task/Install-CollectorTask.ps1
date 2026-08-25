[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigurationPath,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Task209-CollectorTask.Common.ps1")

$configuration = Get-Task209CollectorConfiguration `
    -ConfigurationPath $ConfigurationPath `
    -RequireResolved:$Apply
$task = Split-Task209TaskName -TaskName $configuration.task_name

if (-not $Apply) {
    [pscustomobject]@{
        action = "preview_install"
        task_path = $task.TaskPath
        task_name = $task.TaskName
        execution_identity = $configuration.execution_identity
        mode = "apply"
        single_instance = $configuration.schedule.multiple_instances
        applied = $false
    } | ConvertTo-Json
    exit 0
}

$existing = Get-ScheduledTask `
    -TaskPath $task.TaskPath `
    -TaskName $task.TaskName `
    -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    throw "Scheduled Task already exists; inspect or uninstall it explicitly."
}

$actionArguments = @(
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy", "AllSigned",
    "-File", ('"{0}"' -f $configuration.runner_script),
    "-ConfigurationPath", ('"{0}"' -f $ConfigurationPath),
    "-Mode", "apply"
) -join " "
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $actionArguments `
    -WorkingDirectory $configuration.working_directory

$triggers = @($configuration.schedule.daily_at_local_times | ForEach-Object {
    $dailyTime = [TimeSpan]::Parse([string]$_)
    $firstRun = (Get-Date).Date.Add($dailyTime)
    if ($firstRun -le (Get-Date)) { $firstRun = $firstRun.AddDays(1) }
    New-ScheduledTaskTrigger -Daily -At $firstRun
})
$principal = New-ScheduledTaskPrincipal `
    -UserId $configuration.execution_sid `
    -LogonType ServiceAccount `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (
        New-TimeSpan -Hours $configuration.schedule.execution_time_limit_hours
    ) `
    -RestartCount $configuration.schedule.restart_count `
    -RestartInterval (
        New-TimeSpan -Minutes $configuration.schedule.restart_interval_minutes
    ) `
    -StartWhenAvailable:$configuration.schedule.start_when_available

if ($PSCmdlet.ShouldProcess($configuration.task_name, "Register Scheduled Task")) {
    Register-ScheduledTask `
        -TaskPath $task.TaskPath `
        -TaskName $task.TaskName `
        -Description $configuration.task_description `
        -Action $action `
        -Trigger $triggers `
        -Principal $principal `
        -Settings $settings | Out-Null
    Disable-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName | Out-Null
}

[pscustomobject]@{
    action = "installed_disabled"
    task_name = $configuration.task_name
    state = "Disabled"
    release_commit = $configuration.release_commit
    trigger_count = $triggers.Count
    principal_sid = $configuration.execution_sid
} | ConvertTo-Json
