param(
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,
    [string]$ContractPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (
    -not [System.IO.Path]::IsPathRooted($ManifestPath) -or
    -not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)
) {
    throw "Release manifest is unavailable."
}
if (-not $ContractPath) {
    $ContractPath = Join-Path $PSScriptRoot "rollback-contract.json"
}
if (-not (Test-Path -LiteralPath $ContractPath -PathType Leaf)) {
    throw "Rollback contract is unavailable."
}

$manifest = Get-Content -LiteralPath $ManifestPath -Raw |
    ConvertFrom-Json
$contract = Get-Content -LiteralPath $ContractPath -Raw |
    ConvertFrom-Json

$requiredComponents = @(
    "application",
    "migration",
    "collector",
    "frontend_assets",
    "scheduled_task"
)
$componentNames = @($contract.components | ForEach-Object { $_.name })
$missingComponents = @(
    $requiredComponents | Where-Object { $_ -notin $componentNames }
)
$classifications = @($contract.migration_classifications)
$unclassifiedMigrations = @(
    @($manifest.changed_migrations) | Where-Object {
        $migrationPath = $_
        @(
            $classifications | Where-Object {
                $_.path -eq $migrationPath
            }
        ).Count -eq 0
    }
)
$checks = [ordered]@{
    source_main_unchanged = [bool]$manifest.source_main_unchanged
    program_worktree_clean = [bool]$manifest.program_worktree_clean
    origin_aligned = [bool]$manifest.origin_aligned
    production_deployed = [bool]$manifest.production_deployed
    production_database_changed = [bool]$manifest.production_database_changed
    required_rollback_components_present = ($missingComponents.Count -eq 0)
    migration_classifications_complete = ($unclassifiedMigrations.Count -eq 0)
}
$pass = (
    $checks.source_main_unchanged -and
    $checks.program_worktree_clean -and
    $checks.origin_aligned -and
    -not $checks.production_deployed -and
    -not $checks.production_database_changed -and
    $checks.required_rollback_components_present -and
    $checks.migration_classifications_complete
)
$report = [ordered]@{
    schema_version = 1
    decision = $(if ($pass) { "PASS" } else { "BLOCKED" })
    checked_at = (Get-Date).ToUniversalTime().ToString("o")
    checks = $checks
    missing_components = $missingComponents
    unclassified_migrations = $unclassifiedMigrations
    actions_executed = @("read-only manifest validation")
    application_rollback_executed = $false
    migration_downgrade_executed = $false
    scheduled_task_changed = $false
}
$report | ConvertTo-Json -Depth 8
if (-not $pass) {
    exit 2
}
