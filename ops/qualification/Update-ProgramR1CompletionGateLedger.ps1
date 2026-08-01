param(
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,
    [Parameter(Mandatory = $true)]
    [ValidateSet("GATE0", "GATE1", "GATE2", "GATE3", "GATE4", "GATE5", "GATE6")]
    [string]$GateId,
    [Parameter(Mandatory = $true)]
    [string]$Status,
    [Parameter(Mandatory = $true)]
    [string]$Execution,
    [Parameter(Mandatory = $true)]
    [string]$Head,
    [Parameter(Mandatory = $true)]
    [string]$Marker,
    [string[]]$Evidence = @(),
    [string]$InvalidationDecision = "none",
    [string]$CurrentGate = "",
    [switch]$ResolveInvalidation,
    [string]$TestRunName = "",
    [int]$Passed = 0,
    [int]$Failed = 0,
    [int]$SubtestsPassed = 0,
    [string]$TestEvidence = "",
    [string]$ExternalPrerequisite = "",
    [string]$WialonScope = "",
    [string]$WialonFeatureFlag = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomAtomic {
    param([string]$Path, [object]$Value)
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    $json = $Value | ConvertTo-Json -Depth 100
    [IO.File]::WriteAllText(
        $temporary,
        $json + [Environment]::NewLine,
        (New-Object Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$resolvedRoot = (Resolve-Path -LiteralPath $EvidenceRoot).Path
if (-not $resolvedRoot.StartsWith(
    "C:\AgroSat_backups\PROGRAM_R1_COMPLETION_RUN\",
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Unexpected completion evidence root."
}
if ($Head -notmatch '^[0-9a-f]{40}$') {
    throw "Invalid Git HEAD."
}

$statePath = Join-Path $resolvedRoot "PROGRAM_R1_COMPLETION_STATE.json"
$gatePath = Join-Path $resolvedRoot "PROGRAM_R1_COMPLETION_GATE_LEDGER.json"
$testPath = Join-Path $resolvedRoot "PROGRAM_R1_COMPLETION_TEST_LEDGER.json"
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$gateLedger = Get-Content -LiteralPath $gatePath -Raw | ConvertFrom-Json
$testLedger = Get-Content -LiteralPath $testPath -Raw | ConvertFrom-Json
$now = [DateTimeOffset]::UtcNow.ToString("o")

$gate = @($gateLedger.gates | Where-Object { $_.id -eq $GateId })
if ($gate.Count -ne 1) {
    throw "Gate ledger does not contain exactly one matching gate."
}
$gate = $gate[0]
$gate.status = $Status
$gate.execution = $Execution
$gate.head = $Head
$gate | Add-Member -NotePropertyName marker -NotePropertyValue $Marker -Force
$gate | Add-Member -NotePropertyName invalidation -NotePropertyValue $InvalidationDecision -Force
$gate | Add-Member -NotePropertyName evidence -NotePropertyValue @($Evidence) -Force
$gate | Add-Member -NotePropertyName productionWrites -NotePropertyValue 0 -Force
if ($ExternalPrerequisite) {
    $gate | Add-Member -NotePropertyName externalPrerequisite -NotePropertyValue $ExternalPrerequisite -Force
}
if ($WialonScope) {
    $gate | Add-Member -NotePropertyName wialonScope -NotePropertyValue $WialonScope -Force
}
if ($WialonFeatureFlag) {
    $gate | Add-Member -NotePropertyName wialonFeatureFlag -NotePropertyValue $WialonFeatureFlag -Force
}
if ($Passed -gt 0 -or $Failed -gt 0 -or $SubtestsPassed -gt 0) {
    $gate | Add-Member -NotePropertyName testCounts -NotePropertyValue ([pscustomobject]@{
        passed = $Passed
        failed = $Failed
        subtestsPassed = $SubtestsPassed
    }) -Force
}

$state.currentHead = $Head
if ($CurrentGate) {
    $state.currentGate = $CurrentGate
}
$state.updatedAt = $now
if ($ResolveInvalidation) {
    $state.invalidatedGates = @($state.invalidatedGates | Where-Object { $_ -ne $GateId })
}
$gateLedger.updatedAt = $now
$testLedger.updatedAt = $now

if ($TestRunName) {
    $existing = @($testLedger.runs | Where-Object {
        $_.gate -eq $GateId -and $_.head -eq $Head -and $_.name -eq $TestRunName
    })
    if ($existing.Count -eq 0) {
        $run = [pscustomobject]@{
            name = $TestRunName
            gate = $GateId
            execution = $Execution
            head = $Head
            status = $(if ($Failed -eq 0) { "PASS" } else { "FAIL" })
            passed = $Passed
            failed = $Failed
            subtestsPassed = $SubtestsPassed
            evidence = $TestEvidence
            productionWrites = 0
        }
        $testLedger.runs = @($testLedger.runs) + @($run)
    }
}

Write-Utf8NoBomAtomic -Path $statePath -Value $state
Write-Utf8NoBomAtomic -Path $gatePath -Value $gateLedger
Write-Utf8NoBomAtomic -Path $testPath -Value $testLedger
