# 기사 감성(KR-FinBERT)을 이 PC 의 GPU 로 매겨 GCP 시연 서버에 올린다. 서버에는 GPU 가 없어서 새 기사는 점수 없이 들어온다.
#   1) 서버: 점수 없는 이벤트 기사를 내보냄 (run.sh sentiment-export)
#   2) 이 PC: nexus/.venv-cuda 로 점수 (python -m nexus sentiment-score)
#   3) 서버: 감성 캐시에 합치고 뉴스 화면 논조를 DB 에 씀 (run.sh sentiment-import)
# 작업 스케줄러가 30분마다 부른다 (register-gpu-sentiment.ps1). 수동 실행: powershell -File deploy\nexus-realtime\gpu-sentiment.ps1
# gcloud 는 진행 상황을 stderr 로 내서 PowerShell 5.1 이 오류로 감싼다 — 멈추지 말고 종료 코드로 판단한다
$ErrorActionPreference = "Continue"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$python = Join-Path $repo "nexus\.venv-cuda\Scripts\python.exe"
$work = Join-Path $env:LOCALAPPDATA "nexus-gpu-sentiment"
$log = Join-Path $work "run.log"
$ssh = @("--zone=asia-northeast3-a", "--project=cosmos-nexus-1026", "--tunnel-through-iap", "--quiet", "--strict-host-key-checking=no")
New-Item -ItemType Directory -Force $work | Out-Null

function Log($msg) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Tee-Object -FilePath $log -Append }

try {
    Log "export"
    gcloud compute ssh nexus-vm @ssh --command="sudo /opt/nexus-pipeline/deploy/nexus-realtime/run.sh sentiment-export" 2>&1 |
        Select-Object -Last 2 | ForEach-Object { Log "  $_" }
    if ($LASTEXITCODE -ne 0) { throw "export failed ($LASTEXITCODE)" }
    $todo = Join-Path $work "todo.jsonl"
    gcloud compute scp "nexus-vm:/tmp/nexus-sentiment-todo.jsonl" $todo @ssh 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "download failed ($LASTEXITCODE)" }
    $count = (Get-Content $todo -Encoding utf8 | Where-Object { $_.Trim() }).Count
    Log "unscored: $count"
    if ($count -eq 0) { exit 0 }

    $scores = Join-Path $work "scores.parquet"
    Push-Location (Join-Path $repo "nexus")
    try {
        & $python -X utf8 -m nexus sentiment-score --input $todo --out $scores 2>&1 | Select-Object -Last 2 | ForEach-Object { Log "  $_" }
        if ($LASTEXITCODE -ne 0) { throw "sentiment-score failed ($LASTEXITCODE)" }
    } finally { Pop-Location }

    gcloud compute scp $scores "nexus-vm:/tmp/nexus-sentiment-scores.parquet" @ssh 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "upload failed ($LASTEXITCODE)" }
    gcloud compute ssh nexus-vm @ssh --command="sudo /opt/nexus-pipeline/deploy/nexus-realtime/run.sh sentiment-import /tmp/nexus-sentiment-scores.parquet" 2>&1 |
        Select-Object -Last 2 | ForEach-Object { Log "  $_" }
    if ($LASTEXITCODE -ne 0) { throw "import failed ($LASTEXITCODE)" }
    Log "done"
} catch {
    Log "failed: $_"
    exit 1
}
