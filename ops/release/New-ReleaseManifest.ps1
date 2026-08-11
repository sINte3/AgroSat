param(
    [string]$ProgramWorktree = "C:\AgroSat_worktrees\program-r3-mega-repair",
    [string]$SourceCheckout = "C:\AgroSat",
    [string]$SourceBaseline = "40e8e379d9d29cb4bfb8afebdd9c489c19756fac",
    [string]$ProgramBranch = "task/program-r3-mega-repair",
    [string]$OutputPath = "",
    [switch]$WriteManifest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-SafeGit {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $output = @(& git -C $Repository @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "Git inspection failed with a nonzero exit code."
    }
    return $output
}

function Get-OptionalGitRef {
    param([Parameter(Mandatory = $true)][string]$Repository, [Parameter(Mandatory = $true)][string]$Ref)
    $output = @(& git -C $Repository rev-parse --verify --quiet $Ref 2>$null)
    if ($LASTEXITCODE -ne 0) { return $null }
    return [string]($output | Select-Object -First 1)
}

if (
    [System.IO.Path]::GetFullPath($ProgramWorktree) -ne
    "C:\AgroSat_worktrees\program-r3-mega-repair"
) {
    throw "Program worktree does not match the PROGRAM R3 contract."
}
if ([System.IO.Path]::GetFullPath($SourceCheckout) -ne "C:\AgroSat") {
    throw "Source checkout does not match the TASK_211 contract."
}
if ($SourceBaseline -notmatch "^[a-f0-9]{40}$") {
    throw "Source baseline is not a full Git SHA."
}

$branch = [string](
    @(Invoke-SafeGit -Repository $ProgramWorktree -Arguments @(
        "rev-parse", "--abbrev-ref", "HEAD"
    )) | Select-Object -First 1
)
$head = [string](
    @(Invoke-SafeGit -Repository $ProgramWorktree -Arguments @(
        "rev-parse", "HEAD"
    )) | Select-Object -First 1
)
$originHead = Get-OptionalGitRef -Repository $ProgramWorktree -Ref "origin/$ProgramBranch"
[array]$programStatus = @(
    Invoke-SafeGit -Repository $ProgramWorktree -Arguments @(
        "status", "--porcelain=v2", "--untracked-files=no"
    )
)
$sourceBranch = [string](
    @(Invoke-SafeGit -Repository $SourceCheckout -Arguments @(
        "rev-parse", "--abbrev-ref", "HEAD"
    )) | Select-Object -First 1
)
$sourceHead = [string](
    @(Invoke-SafeGit -Repository $SourceCheckout -Arguments @(
        "rev-parse", "HEAD"
    )) | Select-Object -First 1
)
$sourceOriginHead = [string](
    @(Invoke-SafeGit -Repository $SourceCheckout -Arguments @(
        "rev-parse", "origin/main"
    )) | Select-Object -First 1
)
[array]$sourceStatus = @(
    Invoke-SafeGit -Repository $SourceCheckout -Arguments @(
        "status", "--porcelain=v2", "--untracked-files=no"
    )
)
[array]$changedFiles = @(
    (Invoke-SafeGit -Repository $ProgramWorktree -Arguments @(
        "diff", "--name-only", "$SourceBaseline..$head"
    )) | Where-Object { $_ }
)
[array]$changedMigrations = @(
    $changedFiles | Where-Object {
        $_.StartsWith(
            "backend/alembic/versions/",
            [System.StringComparison]::Ordinal
        ) -and $_.EndsWith(".py", [System.StringComparison]::Ordinal)
    }
)
$commitCount = [string](
    @(Invoke-SafeGit -Repository $ProgramWorktree -Arguments @(
        "rev-list", "--count", "$SourceBaseline..$head"
    )) | Select-Object -First 1
)

$sourceMainUnchanged = (
    $sourceBranch -eq "main" -and
    $sourceHead -eq $SourceBaseline -and
    $sourceOriginHead -eq $SourceBaseline -and
    $sourceStatus.Count -eq 0
)
$manifest = [ordered]@{
    schema_version = 2
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    source_baseline = $SourceBaseline
    program_branch = $ProgramBranch
    observed_branch = $branch
    program_head = $head
    origin_program_head = $originHead
    program_worktree_clean = ($programStatus.Count -eq 0)
    origin_aligned = ($null -ne $originHead -and $head -eq $originHead)
    source_main_unchanged = $sourceMainUnchanged
    new_commit_count = [int]$commitCount
    changed_file_count = $changedFiles.Count
    changed_files = $changedFiles
    changed_migrations = $changedMigrations
    production_deployed = $false
    production_database_changed = $false
    apply_order = @(
        "validate authorization",
        "validate source archive and sha256",
        "materialize isolated release",
        "isolated database migration",
        "loopback health and tenant smoke",
        "isolated release pointer switch",
        "isolated rollback rehearsal",
        "independent review"
    )
}

if (-not $sourceMainUnchanged) {
    throw "Source main preservation check failed."
}
if ($branch -ne $ProgramBranch) {
    throw "Program branch does not match the PROGRAM R3 contract."
}

if ($WriteManifest) {
    if (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
        throw "Manifest output path must be absolute."
    }
    $root = "C:\AgroSat_backups\PROGRAM_R3_MEGA_RELEASE_REPAIR\"
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
    if (-not $resolvedOutput.StartsWith(
        $root,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Manifest output path is outside the PROGRAM R3 evidence root."
    }
    $parent = Split-Path -Parent $resolvedOutput
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $manifest |
        ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $resolvedOutput -Encoding utf8
}
$manifest | ConvertTo-Json -Depth 8
