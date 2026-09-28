$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
Set-Location -LiteralPath $taskRoot

function Test-Python312([string]$Executable, [string[]]$PrefixArguments) {
    try {
        & $Executable @PrefixArguments -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' *> $null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

$taskPython = $null
$taskPrefix = @()
if ($env:COSMOS_WORKER_PYTHON) {
    if (-not (Test-Python312 $env:COSMOS_WORKER_PYTHON @())) {
        throw 'COSMOS_WORKER_PYTHON must name a working Python 3.12 executable.'
    }
    $taskPython = $env:COSMOS_WORKER_PYTHON
} else {
    $taskCandidates = @(
        (Join-Path $taskRoot '.venv\Scripts\python.exe'),
        (Join-Path $taskRoot 'python\python.exe'),
        'python3.12', 'python3', 'python'
    )
    foreach ($taskCandidate in $taskCandidates) {
        if (Test-Python312 $taskCandidate @()) {
            $taskPython = $taskCandidate
            break
        }
    }
    if (-not $taskPython -and (Test-Python312 'py' @('-3.12'))) {
        $taskPython = 'py'
        $taskPrefix = @('-3.12')
    }
}
if (-not $taskPython) {
    throw 'Python 3.12 is required. Install full Python 3.12 with venv/pip, or set COSMOS_WORKER_PYTHON.'
}
& $taskPython @taskPrefix -u (Join-Path $taskRoot 'bootstrap.py') @args
exit $LASTEXITCODE
