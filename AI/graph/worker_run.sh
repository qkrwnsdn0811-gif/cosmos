#!/bin/bash
# 감성 추론 — Git Bash 에서 이 폴더로 가서 실행한다.
#
#   bash run.sh
#
# 처음 한 번은 가상환경을 만들고 라이브러리를 받는다(인터넷 필요, 약 3GB).
# 두 번째부터는 바로 추론으로 넘어간다.
# 중간에 꺼져도 괜찮다. 다시 실행하면 끝난 문장은 건너뛰고 이어서 한다.
set -u
cd "$(dirname "$0")"
PY_EXE=".venv/Scripts/python.exe"

if [ ! -f "$PY_EXE" ]; then
  echo "[1/3] 가상환경을 만든다 (처음 한 번만)"
  if command -v py >/dev/null 2>&1; then
    py -3.12 -m venv .venv
  elif command -v python >/dev/null 2>&1; then
    python -m venv .venv
  else
    echo "Python 을 찾을 수 없다. Python 3.12 를 설치할 것."
    exit 1
  fi
  "$PY_EXE" -m pip install -q --upgrade pip
  echo "[2/3] 라이브러리를 받는다 — 약 3GB, 몇 분 걸린다"
  if ! "$PY_EXE" -m pip install -q torch --index-url https://download.pytorch.org/whl/cu124; then
    echo "torch 설치 실패. 인터넷 연결을 확인할 것."
    exit 1
  fi
  if ! "$PY_EXE" -m pip install -q transformers pandas pyarrow; then
    echo "라이브러리 설치 실패."
    exit 1
  fi
else
  echo "[1/3] 가상환경 있음 — 건너뜀"
  echo "[2/3] 라이브러리 있음 — 건너뜀"
fi

CUDA=$("$PY_EXE" -c "import torch;print(torch.cuda.is_available())" 2>/dev/null | tr -d "[:space:]")
echo "CUDA 사용 가능: $CUDA"
DEV=cuda
if [ "$CUDA" != "True" ]; then
  echo "GPU 를 못 찾았다. CPU 는 매우 느리다 — 수십 시간 걸린다."
  echo "NVIDIA 드라이버를 확인하고 다시 실행하는 것을 권한다."
  read -r -p "그래도 CPU 로 진행하려면 Enter, 중단하려면 Ctrl+C: " _
  DEV=cpu
fi

echo "[3/3] 추론 시작 — GPU 로 약 1시간 40분 (231만 문장)"
PYTHONUTF8=1 "$PY_EXE" infer_sentence_sentiment.py \
  --src data/shard1 --out data/labels_shard1.parquet --device "$DEV"
RC=$?
if [ $RC -ne 0 ]; then
  echo "추론 실패 (rc=$RC). 위 오류를 확인할 것."
  exit $RC
fi

echo
echo "끝났다. data/labels_shard1.parquet 를 돌려줄 것."
