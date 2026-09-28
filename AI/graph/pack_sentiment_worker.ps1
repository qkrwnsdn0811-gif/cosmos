# 두 번째 노트북용 감성 추론 패키지를 만든다.
#
#   powershell -ExecutionPolicy Bypass -File pack_sentiment_worker.ps1
#
# 만들어지는 것: sentiment_worker.zip (약 900MB)
#   worker/
#     run.sh / run.ps1              Git Bash 면 bash run.sh, PowerShell 이면 run.ps1
#     infer_sentence_sentiment.py   독립 실행형 (다른 로컬 모듈 import 안 함)
#     requirements.txt
#     models/sentiment_ko/          KR-FinBert-SC  (pytorch 만)
#     models/sentiment_en/          ProsusAI/finbert (pytorch 만 — tf/flax 사본 제외)
#     data/shard1/                  담당 샤드
#
# tf_model.h5 와 flax_model.msgpack 은 각각 418MB 인데 추론에 쓰지 않는다. 빼면 836MB 준다.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$work = Join-Path $here "worker"
$zip  = Join-Path $here "sentiment_worker.zip"

if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force -Path $work | Out-Null

Write-Host "[1/4] 스크립트 복사"
foreach ($f in @("infer_sentence_sentiment.py")) {   # 독립 실행형 — 다른 로컬 모듈 불필요
    Copy-Item (Join-Path $here $f) $work
}

Write-Host "[2/4] 모델 복사 (추론에 필요한 파일만)"
# 추론에 필요한 것: config.json, 토크나이저, pytorch 가중치 하나
$keep = @("config.json", "pytorch_model.bin", "tokenizer.json", "tokenizer_config.json",
          "special_tokens_map.json", "vocab.txt", "merges.txt")
foreach ($m in @("sentiment_ko", "sentiment_en")) {
    $dst = Join-Path $work "models\$m"
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    foreach ($k in $keep) {
        $src = Join-Path $here "local_models\$m\$k"
        if (Test-Path $src) { Copy-Item $src $dst }
    }
    $mb = [math]::Round((Get-ChildItem $dst | Measure-Object Length -Sum).Sum / 1MB)
    Write-Host "      $m  $mb MB"
}

Write-Host "[3/4] 샤드 복사"
$shard = Join-Path $here "data\shard1"
if (-not (Test-Path $shard)) {
    Write-Host "      data\shard1 이 없다. 먼저 서버에서 내려받을 것:" -ForegroundColor Yellow
    Write-Host "      hdfs dfs -get /data-lake/sandbox/news/junwoo/sentiment-shards/v1/shard=1 ..." -ForegroundColor Yellow
    throw "data\shard1 없음"
}
$dstShard = Join-Path $work "data\shard1"
New-Item -ItemType Directory -Force -Path $dstShard | Out-Null
Copy-Item "$shard\*.parquet" $dstShard

Write-Host "[4/4] 실행 파일 작성"
@'
torch --index-url https://download.pytorch.org/whl/cu124
transformers
pandas
pyarrow
'@ | Set-Content -Path (Join-Path $work "requirements.txt") -Encoding utf8

# run.sh 와 README 는 별도 파일로 둔다.
# PowerShell 5.1 은 BOM 없는 UTF-8 .ps1 을 ANSI 로 읽어 here-string 안의 한글을 깨뜨린다.
# 실제로 한 번 깨져서 bash 구문 오류가 났다. 파일로 두고 복사만 한다.
Copy-Item (Join-Path $here "worker_run.sh")     (Join-Path $work "run.sh")
Copy-Item (Join-Path $here "worker_run.ps1")    (Join-Path $work "run.ps1")
Copy-Item (Join-Path $here "worker_README.txt") (Join-Path $work "README.txt")

Write-Host "압축 중..."
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path "$work\*" -DestinationPath $zip -CompressionLevel Optimal

$mb = [math]::Round((Get-Item $zip).Length / 1MB)
Write-Host ""
Write-Host "완료: $zip  ($mb MB)" -ForegroundColor Green
Write-Host "두 번째 노트북에 복사하고 README.txt 대로 실행할 것."
