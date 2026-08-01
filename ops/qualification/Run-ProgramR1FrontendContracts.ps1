param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceDirectory,
    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot,
    [Parameter(Mandatory = $true)]
    [string]$ChromiumPath,
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedHead,
    [int]$CdpPort = 59321,
    [string]$WorktreeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-JsonAtomic {
    param([string]$Path, [object]$Value)
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText(
        $temporary,
        ($Value | ConvertTo-Json -Depth 20),
        (New-Object Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Invoke-NpmContract {
    param([string]$Name, [bool]$Browser)
    $contractEvidence = Join-Path $EvidenceDirectory $Name.Replace(":", "_")
    New-Item -ItemType Directory -Path $contractEvidence -Force | Out-Null
    $start = [DateTimeOffset]::UtcNow
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $script:NodePath
    $info.Arguments = "`"$script:NpmCliPath`" run $Name --silent"
    $info.WorkingDirectory = Join-Path $WorktreeRoot "frontend"
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.EnvironmentVariables["TASK209_BASE_URL"] = $BaseUrl
    $info.EnvironmentVariables["TASK209_CDP_PORT"] = [string]$CdpPort
    $info.EnvironmentVariables["TASK209_EVIDENCE_DIR"] = $contractEvidence
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $null = $process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    $duration = [Math]::Round(([DateTimeOffset]::UtcNow - $start).TotalSeconds, 3)
    $failureTail = @()
    if ($process.ExitCode -ne 0) {
        $failureTail = @(
            (($stdout + "`n" + $stderr) -split "`r?`n") |
                Where-Object { $_ } |
                Select-Object -Last 12 |
                ForEach-Object {
                    $_ -replace '(?i)(authorization|token|password|secret|api[_-]?key)\s*[:=]\s*\S+', '$1=[REDACTED]'
                }
        )
    }
    return [ordered]@{
        name = $Name
        browserFixtureContract = $Browser
        status = $(if ($process.ExitCode -eq 0) { "PASS" } else { "FAIL" })
        exitCode = $process.ExitCode
        durationSeconds = $duration
        evidenceDirectory = $contractEvidence
        stdoutPersisted = $false
        stderrPersisted = $false
        failureTail = $failureTail
    }
}

$resolvedEvidence = [IO.Path]::GetFullPath($EvidenceDirectory)
$resolvedRuntime = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$resolvedChromium = (Resolve-Path -LiteralPath $ChromiumPath).Path
if (-not $resolvedEvidence.StartsWith("C:\AgroSat_backups\PROGRAM_R1_COMPLETION_RUN\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Frontend contract evidence must remain under the completion root."
}
if ($resolvedRuntime.StartsWith("C:\AgroSat_backups\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Browser profile must remain outside evidence."
}
$actualHead = (& git -C $WorktreeRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualHead -ne $ExpectedHead) {
    throw "Unexpected worktree HEAD."
}
$npmPath = (Get-Command npm.cmd -ErrorAction Stop).Source
$script:NodePath = (Get-Command node.exe -ErrorAction Stop).Source
$script:NpmCliPath = Join-Path (Split-Path $npmPath) "node_modules\npm\bin\npm-cli.js"
if (-not (Test-Path -LiteralPath $script:NpmCliPath -PathType Leaf)) {
    throw "npm CLI entry point is unavailable."
}
New-Item -ItemType Directory -Path $resolvedEvidence -Force | Out-Null

$staticContracts = @(
    "test:closure",
    "test:executive",
    "test:vector-raster",
    "test:index-request-lifecycle",
    "test:enterprise-ndvi-history",
    "test:program-r1-accessibility",
    "test:pixel-anomaly",
    "test:offline-scouting",
    "test:wialon-read-only",
    "test:weather-irrigation",
    "test:yield-map-import",
    "test:productivity-zones",
    "test:variable-rate",
    "test:commercial-tenant"
)
$browserContracts = @(
    "test:closure-browser",
    "test:executive-browser",
    "test:vector-raster-browser",
    "test:wialon-read-only-browser",
    "test:weather-irrigation-browser",
    "test:yield-map-import-browser",
    "test:productivity-zones-browser",
    "test:commercial-tenant-browser"
)

$results = @()
$profiles = @()
foreach ($name in $staticContracts) {
    $results += Invoke-NpmContract -Name $name -Browser $false
}

$browserIndex = 0
foreach ($name in $browserContracts) {
    $browserIndex += 1
    $profile = Join-Path $resolvedRuntime "frontend-contract-cdp-profile-$browserIndex"
    $profiles += $profile
    $resolvedProfile = [IO.Path]::GetFullPath($profile)
    if (-not $resolvedProfile.StartsWith($resolvedRuntime + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe browser profile target."
    }
    if (Test-Path -LiteralPath $resolvedProfile) {
        Remove-Item -LiteralPath $resolvedProfile -Recurse -Force
    }
    $chrome = $null
    try {
        $chrome = Start-Process -FilePath $resolvedChromium -ArgumentList @(
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=$CdpPort",
            "--user-data-dir=$resolvedProfile",
            "about:blank"
        ) -PassThru -WindowStyle Hidden
        $ready = $false
        for ($attempt = 0; $attempt -lt 120; $attempt++) {
            try {
                $null = Invoke-RestMethod -Uri "http://127.0.0.1:$CdpPort/json/version" -TimeoutSec 1
                $ready = $true
                break
            } catch {
                Start-Sleep -Milliseconds 250
            }
        }
        if (-not $ready) {
            throw "Chromium CDP did not become ready."
        }
        $results += Invoke-NpmContract -Name $name -Browser $true
    } finally {
        $scopedChromeProcesses = @(
            Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
                Where-Object {
                    $_.CommandLine -and
                    $_.CommandLine.IndexOf($resolvedProfile, [StringComparison]::OrdinalIgnoreCase) -ge 0
                }
        )
        foreach ($item in $scopedChromeProcesses) {
            Stop-Process -Id ([int]$item.ProcessId) -Force -ErrorAction SilentlyContinue
        }
        if ($null -ne $chrome -and -not $chrome.HasExited) {
            Stop-Process -Id $chrome.Id -Force -ErrorAction SilentlyContinue
        }
        for ($attempt = 0; $attempt -lt 100; $attempt++) {
            if (-not (Get-NetTCPConnection -LocalPort $CdpPort -State Listen -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 100
        }
        if (Get-NetTCPConnection -LocalPort $CdpPort -State Listen -ErrorAction SilentlyContinue) {
            throw "Scoped Chromium CDP process did not stop."
        }
        if (Test-Path -LiteralPath $resolvedProfile) {
            Remove-Item -LiteralPath $resolvedProfile -Recurse -Force -ErrorAction Stop
        }
    }
}

$failed = @($results | Where-Object { $_.status -ne "PASS" })
$report = [ordered]@{
    schemaVersion = 1
    gate = "GATE5"
    head = $actualHead
    baseUrlClass = "isolated_loopback_production_preview"
    staticContractCount = $staticContracts.Count
    browserFixtureContractCount = $browserContracts.Count
    fixtureBrowserContractsAreNotLiveProductEvidence = $true
    results = $results
    browserProfileCleaned = (@($profiles | Where-Object { Test-Path -LiteralPath $_ }).Count -eq 0)
    credentialsIncluded = $false
    productionWrites = 0
    status = $(if ($failed.Count -eq 0) { "PASS" } else { "FAIL" })
    marker = $(if ($failed.Count -eq 0) { "PASS_GATE5_ALL_FRONTEND_CONTRACTS" } else { "FAIL_GATE5_FRONTEND_CONTRACTS" })
}
Write-JsonAtomic -Path (Join-Path $resolvedEvidence "FRONTEND_CONTRACTS.json") -Value $report
Write-Output "FRONTEND_CONTRACTS=$($report.status); COUNT=$($results.Count); HEAD=$actualHead"
if ($failed.Count -ne 0) { exit 2 }
