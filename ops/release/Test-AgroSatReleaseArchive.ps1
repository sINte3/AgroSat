[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ReleaseCandidate,
    [string]$ExpectedBranch = 'task/program-r3-macrostage-f-closed-loop-agronomy',
    [string]$DestinationPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-SafeArchiveEntryName {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ([string]::IsNullOrWhiteSpace($Name) -or [IO.Path]::IsPathRooted($Name) -or $Name.StartsWith('/') -or $Name.StartsWith('\\')) { return $false }
    foreach ($segment in $Name.Replace('\\', '/').Split('/')) {
        if ([string]::IsNullOrWhiteSpace($segment) -or $segment -in @('.', '..') -or $segment.Contains(':') -or $segment.EndsWith(' ') -or $segment.EndsWith('.')) { return $false }
        if ($segment -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$') { return $false }
    }
    return $true
}

if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) { throw 'RELEASE_ARCHIVE_MISSING' }
$resolvedArchive = [IO.Path]::GetFullPath($ArchivePath)
$actualSha256 = (Get-FileHash -LiteralPath $resolvedArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -cne $ExpectedSha256.ToLowerInvariant()) { throw 'RELEASE_ARCHIVE_HASH_MISMATCH' }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($resolvedArchive)
try {
    $names = @($archive.Entries | ForEach-Object FullName)
    if (@($names | Group-Object | Where-Object Count -gt 1).Count -ne 0) { throw 'RELEASE_ARCHIVE_DUPLICATE_PATH' }
    foreach ($name in $names) {
        if (-not (Test-SafeArchiveEntryName -Name $name.TrimEnd([char[]]@('/')))) { throw "RELEASE_ARCHIVE_UNSAFE_PATH:$name" }
    }
    $manifestEntry = $archive.Entries | Where-Object FullName -eq 'release-manifest.json' | Select-Object -First 1
    if ($null -eq $manifestEntry) { throw 'RELEASE_ARCHIVE_MANIFEST_MISSING' }
    $reader = [IO.StreamReader]::new($manifestEntry.Open())
    try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json } catch { throw 'RELEASE_ARCHIVE_MANIFEST_MALFORMED' } finally { $reader.Dispose() }
    foreach ($field in @('schema_version', 'git_sha', 'branch', 'accepted_source_baseline', 'created_utc')) {
        if ($null -eq $manifest.PSObject.Properties[$field]) { throw "RELEASE_ARCHIVE_MANIFEST_FIELD_MISSING:$field" }
    }
    if ($manifest.git_sha -cne $ReleaseCandidate) { throw 'RELEASE_ARCHIVE_GIT_SHA_MISMATCH' }
    if ($manifest.branch -cne $ExpectedBranch) { throw 'RELEASE_ARCHIVE_BRANCH_MISMATCH' }

    if ($DestinationPath) {
        $destination = [IO.Path]::GetFullPath($DestinationPath)
        if (Test-Path -LiteralPath $destination) { throw 'RELEASE_ARCHIVE_DESTINATION_EXISTS' }
        foreach ($entry in $archive.Entries) {
            $target = [IO.Path]::GetFullPath((Join-Path $destination $entry.FullName))
            if (-not $target.StartsWith($destination + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'RELEASE_ARCHIVE_EXTRACTION_ESCAPE' }
        }
        New-Item -ItemType Directory -Path $destination -ErrorAction Stop | Out-Null
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName.EndsWith('/')) { New-Item -ItemType Directory -Path (Join-Path $destination $entry.FullName) -Force | Out-Null; continue }
            $target = [IO.Path]::GetFullPath((Join-Path $destination $entry.FullName))
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            $input = $entry.Open(); $output = [IO.File]::Create($target)
            try { $input.CopyTo($output) } finally { $output.Dispose(); $input.Dispose() }
        }
    }
    [ordered]@{ status='PASS'; archive=$resolvedArchive; sha256=$actualSha256; release_candidate=$ReleaseCandidate; entry_count=$archive.Entries.Count; extracted=[bool]$DestinationPath; production_or_staging_mutated=$false } | ConvertTo-Json -Compress
} finally { $archive.Dispose() }
