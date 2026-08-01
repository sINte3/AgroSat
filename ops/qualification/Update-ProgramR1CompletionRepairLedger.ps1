param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,
    [Parameter(Mandatory = $true)]
    [string]$RepairCommit,
    [Parameter(Mandatory = $true)]
    [string]$Subject,
    [Parameter(Mandatory = $true)]
    [string]$Defect,
    [Parameter(Mandatory = $true)]
    [string]$Gate,
    [string[]]$Evidence = @(),
    [string]$Invalidation = "accepted Gate 0-2 surfaces unaffected",
    [string[]]$InvalidatedGates = @(),
    [string]$TestSummaryJson = "[]",
    [string]$WorktreeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Value
    )
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    $json = $Value | ConvertTo-Json -Depth 30
    [IO.File]::WriteAllText($temporary, $json, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$resolvedRoot = (Resolve-Path -LiteralPath $EvidenceRoot).Path
if (-not $resolvedRoot.StartsWith("C:\AgroSat_backups\PROGRAM_R1_COMPLETION_RUN\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Completion ledgers must stay under the approved evidence root."
}
if ($RepairCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Repair commit must be a full Git object id."
}
$actualHead = (& git -C $WorktreeRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualHead -ne $RepairCommit) {
    throw "Repair commit must equal the exact current worktree HEAD."
}
$tests = $TestSummaryJson | ConvertFrom-Json
if ($null -eq $tests) { $tests = @() }
$now = [DateTimeOffset]::UtcNow.ToString("o")

$statePath = Join-Path $resolvedRoot "PROGRAM_R1_COMPLETION_STATE.json"
$gatePath = Join-Path $resolvedRoot "PROGRAM_R1_COMPLETION_GATE_LEDGER.json"
$commitPath = Join-Path $resolvedRoot "PROGRAM_R1_COMPLETION_COMMIT_LEDGER.json"
$testPath = Join-Path $resolvedRoot "PROGRAM_R1_COMPLETION_TEST_LEDGER.json"

$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$state.updatedAt = $now
$state.currentHead = $RepairCommit
$state.currentGate = $Gate
$state | Add-Member -NotePropertyName invalidatedGates -NotePropertyValue @($InvalidatedGates) -Force
$state.repairs = @($state.repairs | Where-Object {
    $_.commit -ne $RepairCommit -and $_.subject -ne $Subject
}) + @([pscustomobject]@{
    commit = $RepairCommit
    subject = $Subject
    defect = $Defect
    gate = $Gate
    evidence = @($Evidence)
    invalidation = $Invalidation
    productionWrites = 0
})
Write-JsonAtomic -Path $statePath -Value $state

$commits = Get-Content -LiteralPath $commitPath -Raw | ConvertFrom-Json
$commits.updatedAt = $now
$commits.completionRepairCommits = @($commits.completionRepairCommits | Where-Object {
    $_.commit -ne $RepairCommit -and $_.subject -ne $Subject
}) + @([pscustomobject]@{
    commit = $RepairCommit
    subject = $Subject
    defect = $Defect
    gate = $Gate
    evidence = @($Evidence)
    invalidation = $Invalidation
    pushedToProgramBranch = $true
    productionWrites = 0
})
Write-JsonAtomic -Path $commitPath -Value $commits

$gates = Get-Content -LiteralPath $gatePath -Raw | ConvertFrom-Json
$gates.updatedAt = $now
foreach ($item in $gates.gates) {
    if ($item.id -in $InvalidatedGates) {
        $item.status = "invalidated"
        $item | Add-Member -NotePropertyName invalidation -NotePropertyValue $Invalidation -Force
        $item | Add-Member -NotePropertyName invalidatedByCommit -NotePropertyValue $RepairCommit -Force
        $item | Add-Member -NotePropertyName head -NotePropertyValue $RepairCommit -Force
    } elseif ($item.id -in @("GATE0", "GATE1", "GATE2")) {
        $item | Add-Member -NotePropertyName invalidation -NotePropertyValue "none; $Invalidation" -Force
    }
    if ($item.id -eq $Gate) {
        $item.status = "in_progress"
        $item | Add-Member -NotePropertyName head -NotePropertyValue $RepairCommit -Force
        $item | Add-Member -NotePropertyName repairCommit -NotePropertyValue $RepairCommit -Force
        $item | Add-Member -NotePropertyName repairEvidence -NotePropertyValue @($Evidence) -Force
        $item | Add-Member -NotePropertyName productionWrites -NotePropertyValue 0 -Force
    }
    if ($item.id -in @("GATE3", "GATE5", "GATE6") -and $item.status -eq "pending") {
        $item | Add-Member -NotePropertyName head -NotePropertyValue $RepairCommit -Force
    }
}
Write-JsonAtomic -Path $gatePath -Value $gates

$testLedger = Get-Content -LiteralPath $testPath -Raw | ConvertFrom-Json
$testLedger.updatedAt = $now
$testNames = @($tests | ForEach-Object { $_.name })
$existing = @($testLedger.runs | Where-Object {
    $property = $_.PSObject.Properties["repairCommit"]
    $nameProperty = $_.PSObject.Properties["name"]
    ($null -eq $property -or $property.Value -ne $RepairCommit) -and
    ($null -eq $nameProperty -or $nameProperty.Value -notin $testNames)
})
$additions = @()
foreach ($test in @($tests)) {
    $test | Add-Member -NotePropertyName gate -NotePropertyValue $Gate -Force
    $test | Add-Member -NotePropertyName execution -NotePropertyValue "completion_repair" -Force
    $test | Add-Member -NotePropertyName head -NotePropertyValue $RepairCommit -Force
    $test | Add-Member -NotePropertyName repairCommit -NotePropertyValue $RepairCommit -Force
    $test | Add-Member -NotePropertyName productionWrites -NotePropertyValue 0 -Force
    $additions += $test
}
$testLedger.runs = $existing + $additions
Write-JsonAtomic -Path $testPath -Value $testLedger

Write-Output "COMPLETION_REPAIR_LEDGERS_UPDATED=$RepairCommit; PRODUCTION_WRITES=0"
