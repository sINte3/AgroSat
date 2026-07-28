[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigurationPath,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Task209-CollectorTask.Common.ps1")

$configuration = Get-Task209CollectorConfiguration -ConfigurationPath $ConfigurationPath
$task = Split-Task209TaskName -TaskName $configuration.task_name
if (-not $Apply) {
    [pscustomobject]@{
        action = "preview_uninstall"
        task_name = $configuration.task_name
        applied = $false
    } | ConvertTo-Json
    exit 0
}

if ($PSCmdlet.ShouldProcess($configuration.task_name, "Unregister Scheduled Task")) {
    Unregister-ScheduledTask `
        -TaskPath $task.TaskPath `
        -TaskName $task.TaskName `
        -Confirm:$false
}
