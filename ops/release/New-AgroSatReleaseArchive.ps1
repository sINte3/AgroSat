[CmdletBinding()]
param(
    [string]$ProgramWorktree = 'C:\AgroSat_worktrees\program-r3-macrostage-e-autonomous-monitoring',
    [string]$ProgramBranch = 'task/program-r3-macrostage-e-autonomous-monitoring',
    [string]$AcceptedSourceBaseline = 'f2a12f92f58829d9dfc2ef642c805175d863b34f',
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ReleaseCandidate,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$worktree = [IO.Path]::GetFullPath($ProgramWorktree)
$output = [IO.Path]::GetFullPath($OutputDirectory)
$allowedWorktrees = @(
    'C:\AgroSat_worktrees\program-r3-mega-repair',
    'C:\AgroSat_worktrees\program-r3-macrostage-d-production-release',
    'C:\AgroSat_worktrees\program-r3-macrostage-e-autonomous-monitoring'
)
if ($worktree -notin $allowedWorktrees) { throw 'RELEASE_ARCHIVE_WORKTREE_MISMATCH' }
if ($output.StartsWith($worktree + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'RELEASE_ARCHIVE_OUTPUT_INSIDE_WORKTREE' }
$allowedOutputRoots = @(
    'C:\AgroSat_backups\PROGRAM_R3_FAST_TRACK_RELEASE_CANDIDATE\RUNS\',
    'C:\AgroSat_backups\PROGRAM_R3_MACROSTAGE_D_PRODUCTION_RELEASE\RUNS\',
    'C:\AgroSat_backups\PROGRAM_R3_MACROSTAGE_E_AUTONOMOUS_MONITORING\RUNS\'
)
if (@($allowedOutputRoots | Where-Object { $output.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }).Count -eq 0) { throw 'RELEASE_ARCHIVE_OUTPUT_OUTSIDE_EVIDENCE_ROOT' }
if ((& git -C $worktree rev-parse HEAD).Trim() -cne $ReleaseCandidate) { throw 'RELEASE_ARCHIVE_CANDIDATE_NOT_HEAD' }
if ((& git -C $worktree rev-parse "origin/$ProgramBranch").Trim() -cne $ReleaseCandidate) { throw 'RELEASE_ARCHIVE_ORIGIN_NOT_ALIGNED' }
if (@(& git -C $worktree status --porcelain=v1 --untracked-files=all).Count -ne 0) { throw 'RELEASE_ARCHIVE_WORKTREE_NOT_CLEAN' }

$tracked = @(& git -C $worktree ls-tree -r --name-only $ReleaseCandidate)
if (@($tracked | Where-Object { $_ -match '(^|/)(\.git|\.env($|\.)|node_modules|venv|\.venv|\.pytest_cache|coverage|htmlcov)(/|$)' }).Count -ne 0) { throw 'RELEASE_ARCHIVE_EXCLUDED_CONTENT_TRACKED' }
New-Item -ItemType Directory -Path $output -Force | Out-Null
$timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
$archivePath = Join-Path $output "AgroSat_PROGRAM_R3_RC_$($ReleaseCandidate.Substring(0, 12))_$timestamp.zip"
if (Test-Path -LiteralPath $archivePath) { throw 'RELEASE_ARCHIVE_ALREADY_EXISTS' }
& git -C $worktree archive --format=zip --output=$archivePath $ReleaseCandidate
if ($LASTEXITCODE -ne 0) { throw 'RELEASE_ARCHIVE_GIT_ARCHIVE_FAILED' }

$manifest = [ordered]@{ schema_version=1; git_sha=$ReleaseCandidate; branch=$ProgramBranch; accepted_source_baseline=$AcceptedSourceBaseline; created_utc=(Get-Date).ToUniversalTime().ToString('o'); archive_contract_version=1; first_pilot_feature_state=@{ sentinel='ENABLED'; wialon='DISABLED'; telegram='DISABLED'; mock_mode='DISABLED_FAIL_CLOSED'; web_embedded_scheduler='DISABLED'; collectors='SEPARATE_CLI_ONLY' } }
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
try {
    $manifestPath = Join-Path $tempRoot 'release-manifest.json'
    $inventoryPath = Join-Path $tempRoot 'release-inventory.txt'
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    ($tracked + @('release-manifest.json', 'release-inventory.txt') | Sort-Object) | Set-Content -LiteralPath $inventoryPath -Encoding utf8
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::Open($archivePath, [IO.Compression.ZipArchiveMode]::Update)
    try { [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $manifestPath, 'release-manifest.json') | Out-Null; [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $inventoryPath, 'release-inventory.txt') | Out-Null } finally { $zip.Dispose() }
} finally { Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue }
$hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
[ordered]@{ status='PASS'; archive_path=$archivePath; archive_sha256=$hash; release_candidate=$ReleaseCandidate; production_or_staging_mutated=$false } | ConvertTo-Json -Compress
