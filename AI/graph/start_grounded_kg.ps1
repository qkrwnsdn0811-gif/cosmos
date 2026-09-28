param(
    [switch]$Resume,
    [string]$OutputName = 'kg_grounded_full_20260918_v1',
    [int]$Workers = 4
)
$ErrorActionPreference = 'Stop'
$graphRoot = $PSScriptRoot
$repoRoot = [IO.Path]::GetFullPath((Join-Path $graphRoot '../..'))
$artifactRoot = [IO.Path]::GetFullPath((Join-Path $graphRoot 'artifacts'))
if ($OutputName -notmatch '^kg_grounded_[A-Za-z0-9_]+$') { throw 'Use a simple kg_grounded_ output directory name.' }
$outputPath = Join-Path $artifactRoot $OutputName
$pythonPath = Join-Path $repoRoot 'AI/ner/.venv/Scripts/python.exe'
$scriptPath = Join-Path $graphRoot 'build_grounded_kg.py'
$sourcePath = Join-Path $artifactRoot 'kg_b_partial_merged_20260918_v1'
$launchFile = Join-Path $artifactRoot ($OutputName + '.launcher.json')
if (Test-Path -LiteralPath $launchFile) {
    $previousLaunch = Get-Content -LiteralPath $launchFile -Raw | ConvertFrom-Json
    $previousProcess = Get-Process -Id $previousLaunch.pid -ErrorAction SilentlyContinue
    if ($previousProcess -and $previousProcess.StartTime.ToUniversalTime().ToString('o') -eq $previousLaunch.process_started_at) {
        throw 'The recorded process is still running. Check status.json first.'
    }
}
if ($Resume) {
    if (-not (Test-Path -LiteralPath (Join-Path $outputPath 'protocol.json'))) { throw 'No existing protocol to resume.' }
    $stopPath = Join-Path $outputPath 'STOP'
    if (Test-Path -LiteralPath $stopPath) { Remove-Item -LiteralPath $stopPath }
} elseif (Test-Path -LiteralPath $outputPath) { throw 'Output already exists. Use -Resume or a new output name.' }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$stdoutPath = Join-Path $artifactRoot ($OutputName + '.' + $stamp + '.stdout.log')
$stderrPath = Join-Path $artifactRoot ($OutputName + '.' + $stamp + '.stderr.log')
$arguments = @('-X', 'utf8', ('"' + $scriptPath + '"'), '--merged-dir', ('"' + $sourcePath + '"'),
    '--out', ('"' + $outputPath + '"'), '--workers', [string]$Workers, '--batch-size', '256', '--model-batch-size', '32')
if ($Resume) { $arguments += '--resume' }
$jobProcess = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $repoRoot -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$record = [ordered]@{
    pid=$jobProcess.Id; process_started_at=$jobProcess.StartTime.ToUniversalTime().ToString('o')
    output=$outputPath; stdout=$stdoutPath; stderr=$stderrPath
    started_at=[DateTime]::UtcNow.ToString('o'); resume=[bool]$Resume
}
$record | ConvertTo-Json | Set-Content -LiteralPath $launchFile -Encoding UTF8
$record | ConvertTo-Json
