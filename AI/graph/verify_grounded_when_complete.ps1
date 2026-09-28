param(
    [Parameter(Mandatory = $true)][string]$Dataset,
    [Parameter(Mandatory = $true)][string]$Run,
    [Parameter(Mandatory = $true)][int]$TrainingProcessId,
    [Parameter(Mandatory = $true)][string]$ExpectedVerifierSha,
    [string]$StageFileName = 'independent_verification_stage.json'
)

# One execution dependency for an already running training batch. This is not
# a scheduled task or notification; it exits after one verification or failure.
$ErrorActionPreference = 'Stop'
$graphRoot = $PSScriptRoot
$pythonPath = Join-Path (Split-Path $graphRoot -Parent) 'ner/.venv/Scripts/python.exe'
$verifierPath = Join-Path $graphRoot 'verify_grounded_evaluation.py'
$runPath = [System.IO.Path]::GetFullPath($Run)
$reportPath = Join-Path $runPath 'independent_verification.json'
$evaluationPath = Join-Path $runPath 'evaluation_complete.json'
$completionPath = Join-Path $runPath $StageFileName

function Get-Sha256($path) {
    # Works in Windows PowerShell hosts without module-autoloaded Get-FileHash.
    $stream = [System.IO.File]::OpenRead($path)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}

function Write-StageResult($status, $detail) {
    $body = @{
        status = $status
        detail = $detail
        recorded_at_utc = [DateTime]::UtcNow.ToString('o')
        training_process_id = $TrainingProcessId
        dataset = [System.IO.Path]::GetFullPath($Dataset)
        run = $runPath
        verifier_sha256 = $ExpectedVerifierSha
        notification_created = $false
    } | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($completionPath, $body, [System.Text.UTF8Encoding]::new($false))
    Write-Output $body
}

try {
    Write-Output 'Waiting for this training batch to finish its fixed evaluation.'
    $trainingProcess = Get-Process -Id $TrainingProcessId -ErrorAction SilentlyContinue
    while (-not (Test-Path -LiteralPath $evaluationPath)) {
        if ($null -eq $trainingProcess -or $trainingProcess.HasExited) {
            # The producer writes the completion marker last. A stopped or
            # failed producer must not be reported as a successful training run.
            Write-StageResult 'training_stopped_before_evaluation' 'No completed evaluation marker. Resume the training batch before verification.'
            exit 2
        }
        [void]$trainingProcess.WaitForExit(10000)
        $trainingProcess.Refresh()
    }
    $actualSha = Get-Sha256 $verifierPath
    if ($actualSha -ne $ExpectedVerifierSha.ToLowerInvariant()) {
        throw 'The independent verifier changed after this stage was started.'
    }
    if (-not (Test-Path -LiteralPath $reportPath)) {
        & $pythonPath -X utf8 $verifierPath --dataset $Dataset --run $Run --out $reportPath
        if ($LASTEXITCODE -ne 0) {
            throw "Independent verification exited with code $LASTEXITCODE. Training artifacts were preserved."
        }
    }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    $evaluationSha = Get-Sha256 $evaluationPath
    if ($report.checks_passed -ne $true -or $report.evaluation_manifest_sha256 -ne $evaluationSha) {
        throw 'Independent verification does not match the completed evaluation.'
    }
    if ($report.original_rank_report_fully_passed -eq $false) {
        Write-StageResult 'verified_with_rank_report_correction' 'Classification, HAC comparisons and CPU reloads passed; a separate rank-only report correction was produced. The original rank report remains preserved and did not fully pass.'
    }
    else {
        Write-StageResult 'verified' 'Saved predictions, metrics, paired comparisons and CPU model reloads passed independent checks.'
    }
    exit 0
}
catch {
    Write-StageResult 'verification_failed' $_.Exception.Message
    Write-Error $_
    exit 1
}
