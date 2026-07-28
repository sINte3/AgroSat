[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigurationPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Task209-CollectorTask.Common.ps1")

$configuration = Get-Task209CollectorConfiguration -ConfigurationPath $ConfigurationPath
$task = Split-Task209TaskName -TaskName $configuration.task_name
$registered = Get-ScheduledTask `
    -TaskPath $task.TaskPath `
    -TaskName $task.TaskName `
    -ErrorAction SilentlyContinue

if ($null -eq $registered) {
    [pscustomobject]@{
        installed = $false
        task_name = $configuration.task_name
    } | ConvertTo-Json
    exit 1
}

$info = Get-ScheduledTaskInfo `
    -TaskPath $task.TaskPath `
    -TaskName $task.TaskName
[pscustomobject]@{
    installed = $true
    task_name = $configuration.task_name
    state = [string]$registered.State
    last_run_time = $info.LastRunTime.ToString("o")
    last_task_result = $info.LastTaskResult
    next_run_time = $info.NextRunTime.ToString("o")
    missed_runs = $info.NumberOfMissedRuns
} | ConvertTo-Json
