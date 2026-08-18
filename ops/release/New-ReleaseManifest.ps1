 [CmdletBinding()]
param(
    [string]$ProgramWorktree = "C:\AgroSat_worktrees\program-r3-mega-repair",
    [string]$SourceCheckout = "C:\AgroSat",
    [string]$SourceBaseline = "40e8e379d9d29cb4bfb8afebdd9c489c19756fac",
    [string]$ProgramBranch = "task/program-r3-mega-repair",
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ReleaseCandidate,
    [string]$SourceArchive = "",
    [string]$SourceArchiveSha256 = "",
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

function Get-RequiredGitRef {
    param([Parameter(Mandatory = $true)][string]$Repository, [Parameter(Mandatory = $true)][string]$Ref)
    $output = @(& git -C $Repository rev-parse --verify $Ref 2>$null)
    if ($LASTEXITCODE -ne 0) { throw "GIT_REFERENCE_MISSING:$Ref" }
    return [string]($output | Select-Object -First 1)
}

function Get-RepositoryAlembicHead {
    param([Parameter(Mandatory = $true)][string]$Repository)
    $versionRoot = Join-Path $Repository "backend\alembic\versions"
    $revisions = @{}
    $downRevisions = @{}
    foreach ($file in @(Get-ChildItem -LiteralPath $versionRoot -Filter "*.py" -File)) {
        $content = Get-Content -LiteralPath $file.FullName -Raw
        $revisionMatch = [regex]::Match(
            $content,
            '(?m)^\s*revision(?:\s*:\s*str)?\s*=\s*["''](?<id>[^"'']+)["'']'
        )
        if (-not $revisionMatch.Success) { continue }
        $revision = $revisionMatch.Groups['id'].Value
        if ($revisions.ContainsKey($revision)) { throw "ALEMBIC_DUPLICATE_REVISION:$revision" }
        $revisions[$revision] = $true
        $downBlock = [regex]::Match(
            $content,
            '(?ms)^\s*down_revision(?:\s*:[^=]+)?\s*=\s*(?<value>.*?)(?=^\s*branch_labels\s*=)'
        )
        if ($downBlock.Success) {
            foreach ($match in @([regex]::Matches($downBlock.Groups['value'].Value, '["''](?<id>[^"'']+)["'']'))) {
                $downRevisions[$match.Groups['id'].Value] = $true
            }
        }
    }
    $heads = @($revisions.Keys | Where-Object { -not $downRevisions.ContainsKey($_) } | Sort-Object)
    if ($heads.Count -ne 1) { throw "ALEMBIC_HEAD_COUNT_MISMATCH:$($heads.Count)" }
    return [ordered]@{ head = [string]$heads[0]; count = $heads.Count }
}

function Get-RepositoryRuntimeContract {
    param([Parameter(Mandatory = $true)][string]$Repository)
    $readme = Get-Content -LiteralPath (Join-Path $Repository 'README.md') -Raw
    $dockerfile = Get-Content -LiteralPath (Join-Path $Repository 'backend\Dockerfile') -Raw
    $packageLock = Get-Content -LiteralPath (Join-Path $Repository 'frontend\package-lock.json') -Raw | ConvertFrom-Json
    if ($readme -notmatch 'Python 3\.11\+' -or $readme -notmatch 'Node\.js 18\+') { throw 'REPOSITORY_RUNTIME_CONTRACT_MISSING' }
    if ($dockerfile -notmatch '(?m)^FROM python:3\.11-slim\s*$') { throw 'BACKEND_RUNTIME_IMAGE_CONTRACT_MISMATCH' }
    return [ordered]@{
        python = '3.11+'
        node = '18+'
        backend_image = 'python:3.11-slim'
        npm_lockfile_version = [int]$packageLock.lockfileVersion
        sources = @('README.md', 'backend/Dockerfile', 'frontend/package-lock.json')
    }
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
if (($SourceArchive -and -not $SourceArchiveSha256) -or (-not $SourceArchive -and $SourceArchiveSha256)) { throw "ARCHIVE_IDENTITY_INCOMPLETE" }
if ($SourceArchiveSha256 -and $SourceArchiveSha256 -notmatch "^[a-fA-F0-9]{64}$") { throw "SOURCE_ARCHIVE_HASH_INVALID" }

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
$candidateObject = Get-RequiredGitRef -Repository $ProgramWorktree -Ref "$ReleaseCandidate^{commit}"
$originHead = Get-RequiredGitRef -Repository $ProgramWorktree -Ref "origin/$ProgramBranch"
[array]$programStatus = @(
    Invoke-SafeGit -Repository $ProgramWorktree -Arguments @(
        "status", "--porcelain=v2", "--untracked-files=all"
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
        "status", "--porcelain=v2", "--untracked-files=all"
    )
)
[array]$changedFiles = @(
    (Invoke-SafeGit -Repository $ProgramWorktree -Arguments @(
        "diff", "--name-only", "$SourceBaseline..$ReleaseCandidate"
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
        "rev-list", "--count", "$SourceBaseline..$ReleaseCandidate"
    )) | Select-Object -First 1
)

$sourceMainUnchanged = (
    $sourceBranch -eq "main" -and
    $sourceHead -eq $SourceBaseline -and
    $sourceOriginHead -eq $SourceBaseline -and
    $sourceStatus.Count -eq 0
)
if ($branch -cne $ProgramBranch) { throw "PROGRAM_BRANCH_MISMATCH" }
if ($head -cne $ReleaseCandidate -or $candidateObject -cne $ReleaseCandidate) { throw "RELEASE_CANDIDATE_MUST_EQUAL_CURRENT_HEAD" }
if ($originHead -cne $ReleaseCandidate) { throw "ORIGIN_BRANCH_NOT_ALIGNED" }
if ($programStatus.Count -ne 0) { throw "PROGRAM_WORKTREE_NOT_CLEAN" }
if (-not $sourceMainUnchanged) { throw "SOURCE_MAIN_PRESERVATION_FAILED" }
$archiveHash = $null
$archiveFilename = $null
if ($SourceArchive) {
    if (-not (Test-Path -LiteralPath $SourceArchive -PathType Leaf)) { throw "SOURCE_ARCHIVE_MISSING" }
    $archiveHash = (Get-FileHash -LiteralPath $SourceArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($archiveHash -cne $SourceArchiveSha256.ToLowerInvariant()) { throw "SOURCE_ARCHIVE_HASH_MISMATCH" }
    $archiveFilename = [System.IO.Path]::GetFileName($SourceArchive)
}
$alembic = Get-RepositoryAlembicHead -Repository $ProgramWorktree
$runtimeContract = Get-RepositoryRuntimeContract -Repository $ProgramWorktree
$manifest = [ordered]@{
    schema_version = 3
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    source_baseline = $SourceBaseline
    program_branch = $ProgramBranch
    observed_branch = $branch
    release_candidate = $ReleaseCandidate
    program_head = $head
    origin_program_head = $originHead
    program_worktree_clean = $true
    origin_aligned = $true
    source_main_unchanged = $true
    source_archive_sha256 = $archiveHash
    source_archive_filename = $archiveFilename
    candidate_archive = if ($archiveFilename) { [ordered]@{ filename = $archiveFilename; sha256 = $archiveHash } } else { $null }
    alembic_head = $alembic.head
    alembic_head_count = $alembic.count
    required_runtime_versions = $runtimeContract
    archive_contract_version = 1
    first_pilot_feature_state = [ordered]@{
        sentinel = 'ENABLED'
        wialon = 'DISABLED'
        telegram = 'DISABLED'
        mock_mode = 'DISABLED_FAIL_CLOSED'
        web_embedded_scheduler = 'DISABLED'
        collectors = 'SEPARATE_CLI_ONLY'
    }
    new_commit_count = [int]$commitCount
    changed_file_count = $changedFiles.Count
    changed_files = $changedFiles
    changed_migrations = $changedMigrations
    production_deployed = $false
    production_database_changed = $false
    apply_order = @(
        "validate explicit release candidate and branch alignment",
        "validate Git-native source archive SHA-256 and release manifest",
        "materialize immutable candidate under authorized rehearsal root",
        "apply migrations only to isolated agrosat_r3_rc_ database",
        "launch loopback-only health and tenant/security smoke",
        "switch isolated current-release pointer",
        "validate backup and restore isolated database",
        "restore isolated current-release pointer and rerun health/security smoke"
    )
}

if ($WriteManifest) {
    if (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
        throw "Manifest output path must be absolute."
    }
    $allowedRoots = @(
        "C:\AgroSat_backups\PROGRAM_R3_FAST_TRACK_RELEASE_CANDIDATE\RUNS\",
        "C:\AgroSat_backups\PROGRAM_R3_MEGA_RELEASE_REPAIR\"
    )
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
    if (@($allowedRoots | Where-Object {
        $resolvedOutput.StartsWith($_, [System.StringComparison]::OrdinalIgnoreCase)
    }).Count -eq 0) {
        throw "Manifest output path is outside the PROGRAM R3 evidence root."
    }
    $parent = Split-Path -Parent $resolvedOutput
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $manifest |
        ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $resolvedOutput -Encoding utf8
}
$manifest | ConvertTo-Json -Depth 8
