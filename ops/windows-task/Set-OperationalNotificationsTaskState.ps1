[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)][string]$ConfigurationPath,
    [Parameter(Mandatory = $true)][ValidateSet("Enabled", "Disabled")][string]$State,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Task221-OperationalNotifications.Common.ps1")
$configuration = Get-Task221OperationalNotificationsConfiguration -ConfigurationPath $ConfigurationPath -RequireResolved:$Apply
$task = Split-Task221OperationalNotificationsTaskName -TaskName $configuration.task_name
$registered = Get-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName -ErrorAction SilentlyContinue
if ($null -eq $registered) { throw "Scheduled Task is not installed." }
if ($Apply -and $PSCmdlet.ShouldProcess($configuration.task_name, "Set state to $State")) {
    if ($State -eq "Enabled") {
        Enable-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName | Out-Null
    }
    else {
        Disable-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName | Out-Null
    }
}
[pscustomobject]@{
    task_name = $configuration.task_name
    requested_state = $State
    applied = [bool]$Apply
    release_commit = $configuration.release_commit
} | ConvertTo-Json
