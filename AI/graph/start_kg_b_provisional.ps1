<#
Start or resume the fixed B-model provisional news selection in a hidden process.
This script starts no scheduler, teacher inference, training or service publishing.
The Python worker checks its frozen protocol and durable cursor before resuming.
#>
[CmdletBinding()]
param(
    [ValidateRange(0, 2656806)]
    [int]$MaxPairs = 0,
    [switch]$ResumeAfterStop
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$pythonPath = Join-Path $projectRoot 'AI/ner/.venv/Scripts/python.exe'
$workerPath = Join-Path $PSScriptRoot 'kg_provisional_bulk.py'
$sourceManifest = Join-Path $PSScriptRoot 'artifacts/kg_cascade_v1_20260917_v1/transfer/source/manifest.json'
$checkpointPath = Join-Path $PSScriptRoot 'artifacts/kg_cascade_v2_pairfix_20260917_v1/student_B/attempt_001'
$outputPath = Join-Path $PSScriptRoot 'artifacts/kg_b_provisional_full_20260918_v1'
$launchRoot = Join-Path $PSScriptRoot 'artifacts/kg_b_provisional_launch_20260918_v1'
$stopPath = Join-Path $outputPath 'STOP'

foreach ($requiredPath in @($pythonPath, $workerPath, $sourceManifest, (Join-Path $checkpointPath 'training_manifest.json'))) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file missing: $requiredPath"
    }
}

if (Test-Path -LiteralPath $stopPath -PathType Leaf) {
    if (-not $ResumeAfterStop) {
        throw 'A saved STOP request is present. To explicitly continue, run this script with -ResumeAfterStop.'
    }
    Remove-Item -LiteralPath $stopPath
}

New-Item -ItemType Directory -Path $launchRoot -Force | Out-Null
$launchName = 'launch_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff')
$stdoutPath = Join-Path $launchRoot ($launchName + '.stdout.log')
$stderrPath = Join-Path $launchRoot ($launchName + '.stderr.log')
$resumeRequested = Test-Path -LiteralPath (Join-Path $outputPath 'protocol.json') -PathType Leaf
$workerArguments = @('-X', 'utf8', '-u', $workerPath,
    '--manifest', $sourceManifest, '--checkpoint', $checkpointPath,
    '--out', $outputPath, '--device', 'cuda', '--chunk-size', '32', '--stop-file', $stopPath)
if ($resumeRequested) { $workerArguments += '--resume' }
if ($MaxPairs -gt 0) { $workerArguments += @('--max-pairs', [string]$MaxPairs) }
# These generated arguments contain no embedded quotes; preserve spaces in paths.
if ($workerArguments | Where-Object { $_.Contains('"') }) { throw 'Unsupported quote in launch argument' }
$quotedArguments = ($workerArguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
$process = Start-Process -FilePath $pythonPath -ArgumentList $quotedArguments -WorkingDirectory $projectRoot `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$receipt = [ordered]@{
    started_at = (Get-Date).ToString('o')
    launcher_pid = $process.Id
    arguments = $workerArguments
    resumed = [bool]$resumeRequested
    max_new_pairs = $(if ($MaxPairs -gt 0) { $MaxPairs } else { $null })
    output = $outputPath
    stdout = $stdoutPath
    stderr = $stderrPath
    scope = 'provisional_B_candidate_selection_only'
    production_approved = $false
    notifications_authorized = $false
}
$receiptJson = $receipt | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText((Join-Path $launchRoot ($launchName + '.json')), $receiptJson, [System.Text.UTF8Encoding]::new($false))
$receiptJson
