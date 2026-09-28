# 감성 추론 — PowerShell 에서 이 폴더에서 실행한다.
#
#   powershell -ExecutionPolicy Bypass -File run.ps1
#
# 처음 한 번은 가상환경을 만들고 라이브러리를 받는다(인터넷 필요, 약 3GB).
# 두 번째부터는 바로 추론으로 넘어간다.
# 중간에 꺼져도 괜찮다. 다시 실행하면 끝난 문장은 건너뛰고 이어서 한다.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "[1/3] 가상환경을 만든다 (처음 한 번만)"
    $ok = $false
    foreach ($cmd in @("py -3.12 -m venv .venv", "python -m venv .venv")) {
        try { Invoke-Expression $cmd; $ok = $true; break } catch { }
    }
    if (-not $ok) { throw "Python 을 찾을 수 없다. Python 3.12 를 설치할 것." }

    & $py -m pip install -q --upgrade pip
    Write-Host "[2/3] 라이브러리를 받는다 - 약 3GB, 몇 분 걸린다"
    & $py -m pip install -q torch --index-url https://download.pytorch.org/whl/cu124
    if ($LASTEXITCODE -ne 0) { throw "torch 설치 실패. 인터넷 연결을 확인할 것." }
    & $py -m pip install -q transformers pandas pyarrow
    if ($LASTEXITCODE -ne 0) { throw "라이브러리 설치 실패." }
} else {
    Write-Host "[1/3] 가상환경 있음 - 건너뜀"
    Write-Host "[2/3] 라이브러리 있음 - 건너뜀"
}

$cuda = (& $py -c "import torch;print(torch.cuda.is_available())").Trim()
Write-Host "CUDA 사용 가능: $cuda"
$dev = "cuda"
if ($cuda -ne "True") {
    Write-Host "GPU 를 못 찾았다. CPU 는 매우 느리다 - 수십 시간 걸린다." -ForegroundColor Yellow
    Write-Host "NVIDIA 드라이버를 확인하고 다시 실행하는 것을 권한다." -ForegroundColor Yellow
    Read-Host "그래도 CPU 로 진행하려면 Enter, 중단하려면 Ctrl+C"
    $dev = "cpu"
}

Write-Host "[3/3] 추론 시작 - GPU 로 약 1시간 40분 (231만 문장)"
$env:PYTHONUTF8 = "1"
& $py infer_sentence_sentiment.py --src data\shard1 --out data\labels_shard1.parquet --device $dev
if ($LASTEXITCODE -ne 0) { throw "추론 실패 (rc=$LASTEXITCODE). 위 오류를 확인할 것." }

Write-Host ""
Write-Host "끝났다. data\labels_shard1.parquet 를 돌려줄 것." -ForegroundColor Green
