[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)][string]$ConfigurationPath,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Task221-OperationalNotifications.Common.ps1")

$configuration = Get-Task221OperationalNotificationsConfiguration `
    -ConfigurationPath $ConfigurationPath -RequireResolved:$Apply
$task = Split-Task221OperationalNotificationsTaskName -TaskName $configuration.task_name
if (-not $Apply) {
    [pscustomobject]@{
        action = "preview_install"
        task_name = $configuration.task_name
        interval_minutes = $configuration.schedule.interval_minutes
        release_commit = $configuration.release_commit
        state_after_install = "Disabled"
        applied = $false
    } | ConvertTo-Json
    exit 0
}
if ($null -ne (Get-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName -ErrorAction SilentlyContinue)) {
    throw "Scheduled Task already exists; inspect or uninstall it explicitly."
}
$actionArguments = @(
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "AllSigned",
    "-File", ('"{0}"' -f $configuration.runner_script),
    "-ConfigurationPath", ('"{0}"' -f $ConfigurationPath),
    "-Mode", "apply"
) -join " "
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArguments `
    -WorkingDirectory $configuration.working_directory
$firstRun = (Get-Date).AddMinutes([int]$configuration.schedule.interval_minutes)
$trigger = New-ScheduledTaskTrigger -Once -At $firstRun `
    -RepetitionInterval (New-TimeSpan -Minutes $configuration.schedule.interval_minutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId $configuration.execution_sid `
    -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $configuration.schedule.execution_time_limit_minutes) `
    -RestartCount $configuration.schedule.restart_count `
    -RestartInterval (New-TimeSpan -Minutes $configuration.schedule.restart_interval_minutes) `
    -StartWhenAvailable:$configuration.schedule.start_when_available
if ($PSCmdlet.ShouldProcess($configuration.task_name, "Register disabled Scheduled Task")) {
    Register-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName `
        -Description $configuration.task_description -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings | Out-Null
    Disable-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName | Out-Null
}
[pscustomobject]@{
    action = "installed_disabled"
    task_name = $configuration.task_name
    state = "Disabled"
    release_commit = $configuration.release_commit
    interval_minutes = $configuration.schedule.interval_minutes
} | ConvertTo-Json
