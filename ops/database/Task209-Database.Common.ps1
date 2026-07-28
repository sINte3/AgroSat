Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:Task209EvidenceRoot = "C:\AgroSat_backups\task209_global_program"

function Assert-SafeDatabaseName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabaseName,
        [switch]$RequireTask209Isolation
    )

    if ($DatabaseName -notmatch "^[a-z][a-z0-9_]{2,62}$") {
        throw "Database name does not satisfy the safe identifier contract."
    }
    if ($RequireTask209Isolation -and $DatabaseName -notmatch "^agrosat_task209_[a-z0-9_]+$") {
        throw "Mutable operations require an agrosat_task209_* database."
    }
}

function Resolve-Task209EvidencePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathValue
    )

    if (-not [System.IO.Path]::IsPathRooted($PathValue)) {
        throw "Evidence paths must be absolute."
    }
    $root = [System.IO.Path]::GetFullPath($script:Task209EvidenceRoot).TrimEnd("\") + "\"
    $candidate = [System.IO.Path]::GetFullPath($PathValue)
    if (-not $candidate.StartsWith(
        $root,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Evidence path is outside the TASK_209 backup root."
    }
    return $candidate
}

function Assert-OutsideSanitizedEvidence {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathValue
    )

    $evidenceRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $script:Task209EvidenceRoot "evidence")
    ).TrimEnd("\") + "\"
    $candidate = [System.IO.Path]::GetFullPath($PathValue)
    if ($candidate.StartsWith(
        $evidenceRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "A data-bearing database archive cannot be stored in sanitized evidence."
    }
}

function Require-DatabaseTool {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $command = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Required PostgreSQL client tooling is unavailable."
    }
    return $command.Source
}

function Invoke-CapturedDatabaseTool {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = @(& $Executable @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL client operation failed with a nonzero exit code."
    }
    return $output
}

function Write-SanitizedJsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Value
    )

    $Value |
        ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $Path -Encoding utf8
}
