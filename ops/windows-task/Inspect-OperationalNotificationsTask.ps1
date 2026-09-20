[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ConfigurationPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Task221-OperationalNotifications.Common.ps1")
$configuration = Get-Task221OperationalNotificationsConfiguration -ConfigurationPath $ConfigurationPath
$task = Split-Task221OperationalNotificationsTaskName -TaskName $configuration.task_name
$registered = Get-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName -ErrorAction SilentlyContinue
if ($null -eq $registered) {
    [pscustomobject]@{ installed = $false; task_name = $configuration.task_name } | ConvertTo-Json
    exit 1
}
$info = Get-ScheduledTaskInfo -TaskPath $task.TaskPath -TaskName $task.TaskName
$action = @($registered.Actions)[0]
[pscustomobject]@{
    installed = $true
    task_name = $configuration.task_name
    state = [string]$registered.State
    execute = [string]$action.Execute
    working_directory = [string]$action.WorkingDirectory
    release_commit = $configuration.release_commit
    last_run_time = $info.LastRunTime.ToString("o")
    last_task_result = $info.LastTaskResult
    next_run_time = $info.NextRunTime.ToString("o")
    missed_runs = $info.NumberOfMissedRuns
} | ConvertTo-Json
