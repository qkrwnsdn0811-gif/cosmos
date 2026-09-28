<# Start only this laptop's assigned portable B queue in a hidden process. #>
[CmdletBinding()]
param([switch]$ResumeAfterStop)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$pythonPath = Join-Path $projectRoot 'AI/ner/.venv/Scripts/python.exe'
$splitRoot = Join-Path $PSScriptRoot 'artifacts/kg_b_parallel_20260918_v1'
$packageRoot = Join-Path $splitRoot 'pc1'
$bootstrapPath = Join-Path $packageRoot 'bootstrap.py'
$outputRoot = Join-Path $packageRoot 'output'
$launchRoot = Join-Path $splitRoot 'launches'
foreach ($taskFile in @($pythonPath, $bootstrapPath, (Join-Path $packageRoot 'package_manifest.json'))) {
    if (-not (Test-Path -LiteralPath $taskFile -PathType Leaf)) { throw "Missing file: $taskFile" }
}
$package = Get-Content -LiteralPath (Join-Path $packageRoot 'package_manifest.json') -Raw | ConvertFrom-Json
if ($package.partition -ne 'pc1' -or $package.expected_unique_pairs -ne 877331) {
    throw 'Unexpected laptop partition. Refusing to start.'
}
if ((Test-Path -LiteralPath (Join-Path $outputRoot 'STOP')) -and -not $ResumeAfterStop) {
    throw 'STOP exists. Use -ResumeAfterStop to explicitly continue this queue.'
}
if (Test-Path -LiteralPath $launchRoot) {
    foreach ($taskReceiptFile in Get-ChildItem -LiteralPath $launchRoot -Filter '*.json' -File) {
        $taskReceipt = Get-Content -LiteralPath $taskReceiptFile.FullName -Raw | ConvertFrom-Json
        if ($taskReceipt.partition -ne 'pc1') { continue }
        $taskProcess = Get-Process -Id $taskReceipt.launcher_pid -ErrorAction SilentlyContinue
        if ($taskProcess -and $taskProcess.StartTime.ToUniversalTime().ToString('o') -eq $taskReceipt.process_started_utc) {
            throw "An existing pc1 launcher is active: PID $($taskProcess.Id)"
        }
    }
}
New-Item -ItemType Directory -Path $launchRoot -Force | Out-Null
$taskStamp = 'pc1_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff')
$stdoutPath = Join-Path $launchRoot ($taskStamp + '.stdout.log')
$stderrPath = Join-Path $launchRoot ($taskStamp + '.stderr.log')
$taskArguments = @('-X', 'utf8', '-u', $bootstrapPath, '--skip-install')
if ($ResumeAfterStop) { $taskArguments += '--resume-after-stop' }
if ($taskArguments | Where-Object { $_.Contains('"') }) { throw 'Unsupported quote in argument' }
$quotedArguments = ($taskArguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
$taskProcess = Start-Process -FilePath $pythonPath -ArgumentList $quotedArguments -WorkingDirectory $packageRoot `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$taskReceipt = [ordered]@{
    partition = 'pc1'
    started_at = (Get-Date).ToString('o')
    launcher_pid = $taskProcess.Id
    process_started_utc = $taskProcess.StartTime.ToUniversalTime().ToString('o')
    arguments = $taskArguments
    output = $outputRoot
    stdout = $stdoutPath
    stderr = $stderrPath
    expected_unique_pairs = 877331
    max_new_pairs = $null
    production_approved = $false
    scheduled_task_created = $false
}
$taskJson = $taskReceipt | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText((Join-Path $launchRoot ($taskStamp + '.json')), $taskJson, [System.Text.UTF8Encoding]::new($false))
$taskJson
