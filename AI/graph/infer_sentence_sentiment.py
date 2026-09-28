"""고유 문장에 FinBERT 라벨을 붙인다 (노트북 GPU, 샤드 단위).

FinBERT 는 문장 텍스트만 본다. 같은 문장은 같은 라벨이므로 **고유 텍스트만** 추론하고
(record_id, ticker) 로 되돌려 붙이는 것은 서버에서 한다. 실측 중복 제거율 15%
(545만 행 -> 463만 고유).

  # 서버에서 샤드 내려받기
  hdfs dfs -get /data-lake/sandbox/news/junwoo/sentiment-shards/v1/shard=0 data/shard0

  # 노트북 GPU
  PYTHONUTF8=1 python infer_sentence_sentiment.py --src data/shard0 \
      --out data/labels_shard0.parquet --device cuda

  # 시험
  ... --limit 5000

════════════════════════════════════════════════════════════════════════════
독립 실행형이다 — 다른 로컬 모듈을 import 하지 않는다
════════════════════════════════════════════════════════════════════════════
처음에는 infer_missing_sentiment.Classifier 를 재사용했는데, 그게 lake_sentiment 를
거쳐 news_content 를 부르면서 **로컬 모듈 24개**가 딸려왔다. 워커 노트북에 그걸 다
보낼 이유가 없어서 필요한 것(label_indexes, Classifier)만 여기로 옮겼다.

라벨 순서 검증은 그대로 가져왔다. 두 FinBERT 의 id2label 이 반대라
argmax 를 그냥 쓰면 영문 감성이 통째로 뒤집힌다.

  snunlp/KR-FinBert-SC   0=negative 1=neutral 2=positive
  ProsusAI/finbert       0=positive 1=negative 2=neutral

귀속 가드(_surface)는 여기서 하지 않는다. 그건 (문장, 기업) 판정이라 되돌려 붙일 때
서버에서 한다. 여기서는 문장 하나당 라벨 하나만 낸다.

중단·재개: --out 이 이미 있으면 그 안의 문장은 건너뛴다. 노트북이 꺼져도 이어서 돌린다.
진행은 --report 문장마다 한 줄씩 찍는다.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_VERSION = "evidence-sentence-finbert-v1"
MODELS = {"ko": "sentiment_ko", "en": "sentiment_en"}
LABELS = ("negative", "neutral", "positive")
HANGUL = re.compile(r"[가-힣]")
BATCH = 64


def label_indexes(config: dict) -> dict[str, int]:
    """모델이 실제로 쓰는 라벨 번호를 확인한다. 영문 FinBERT 순서를 추측하지 않는다."""
    raw = config.get("id2label")
    if not isinstance(raw, dict) or len(raw) != 3:
        raise ValueError("id2label 에 감성 3종이 있어야 한다")
    try:
        labels = {str(v).lower(): int(k) for k, v in raw.items()}
    except (TypeError, ValueError) as e:
        raise ValueError("id2label 번호가 잘못됐다") from e
    if set(labels) != set(LABELS) or set(labels.values()) != {0, 1, 2}:
        raise ValueError(f"알 수 없는 라벨 구성: {raw}")
    inverse = config.get("label2id")
    if inverse is not None:
        try:
            inverse = {str(k).lower(): int(v) for k, v in inverse.items()}
        except (AttributeError, TypeError, ValueError) as e:
            raise ValueError("label2id 가 잘못됐다") from e
        if inverse != labels:
            raise ValueError("label2id 와 id2label 이 어긋난다")
    if config.get("num_labels", 3) != 3:
        raise ValueError("출력 라벨이 3개여야 한다")
    return labels


class Classifier:
    def __init__(self, path: Path, device: str, fp16: bool):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch = torch
        self.device = device
        torch.set_num_threads(min(8, max(1, os.cpu_count() or 1)))
        self.tok = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(
            str(path), local_files_only=True).to(device).eval()
        # fp16 은 argmax 결과를 바꾸지 않는다. 실측 179 -> 617 문장/초 (RTX 4050).
        if fp16 and device.startswith("cuda"):
            model = model.half()
        self.model = model
        idx = label_indexes(model.config.to_dict())
        if set(idx) != set(LABELS):
            raise ValueError(f"라벨 집합 불일치: {idx}")
        self.order = [idx[l] for l in LABELS]     # negative, neutral, positive 순

    def predict(self, texts: list[str], report: int = 0, tag: str = "") -> list[str]:
        out: list[str] = []
        t0 = time.time()
        nxt = report
        with self.torch.no_grad():
            for i in range(0, len(texts), BATCH):
                chunk = texts[i:i + BATCH]
                enc = self.tok(chunk, truncation=True, max_length=128,
                               padding=True, return_tensors="pt").to(self.device)
                logits = self.model(**enc).logits[:, self.order]
                out += [LABELS[j].upper() for j in logits.argmax(1).tolist()]
                if report and len(out) >= nxt:
                    el = max(time.time() - t0, 1e-9)
                    rate = len(out) / el
                    left = (len(texts) - len(out)) / max(rate, 1e-9)
                    print(f"  [{tag}] {len(out):,}/{len(texts):,} "
                          f"({len(out)*100//len(texts)}%) "
                          f"{rate:.0f}문장/초  남은시간 {left/60:.0f}분", flush=True)
                    nxt += report
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True, help="샤드 파케이 디렉터리")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", type=int, default=50000, help="진행 출력 간격(문장)")
    ap.add_argument("--models", type=Path, default=None,
                    help="모델 폴더 상위. 기본은 ./models 이고 없으면 ./local_models")
    ap.add_argument("--fp16", action="store_true", default=True)
    ap.add_argument("--no-fp16", dest="fp16", action="store_false")
    args = ap.parse_args()

    import pandas as pd

    root = args.models
    if root is None:
        root = HERE / "models" if (HERE / "models").is_dir() else HERE / "local_models"
    for key, name in MODELS.items():
        if not (root / name).is_dir():
            sys.exit(f"모델 폴더가 없다: {root / name}")

    df = pd.read_parquet(args.src)
    if "sentence_text" not in df.columns:
        sys.exit(f"sentence_text 컬럼이 없다: {list(df.columns)}")
    texts = df["sentence_text"].dropna().astype(str)
    texts = texts[texts.str.strip() != ""].drop_duplicates()

    done: set[str] = set()
    if args.out.exists():
        prev = pd.read_parquet(args.out)
        done = set(prev["sentence_text"])
        print(f"재개: 이미 끝난 {len(done):,}건 건너뜀", flush=True)
    todo = [t for t in texts if t not in done]
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("할 일 없음")
        return

    ko = [t for t in todo if HANGUL.search(t)]
    en = [t for t in todo if not HANGUL.search(t)]
    print(f"대상 {len(todo):,}  (한국어 {len(ko):,} · 영문 {len(en):,})  "
          f"device={args.device} fp16={args.fp16}", flush=True)

    rows = []
    for chunk, key in ((ko, "ko"), (en, "en")):
        if not chunk:
            continue
        print(f"[{key}] 모델을 올린다...", flush=True)
        clf = Classifier(root / MODELS[key], args.device, args.fp16)
        t0 = time.time()
        labels = clf.predict(chunk, report=args.report, tag=key)
        el = time.time() - t0
        print(f"[{key}] 끝 {len(chunk):,}건 {el/60:.1f}분 "
              f"({len(chunk)/max(el,1):.0f}문장/초)", flush=True)
        rows += [{"sentence_text": t, "sentiment": l, "lang": key}
                 for t, l in zip(chunk, labels)]
        del clf

    out = pd.DataFrame(rows)
    if done:
        out = pd.concat([pd.read_parquet(args.out), out], ignore_index=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(args.out, index=False)

    dist = collections.Counter(out["sentiment"])
    print(json.dumps({"out": str(args.out), "rows": len(out),
                      "distribution": dict(dist), "model_version": MODEL_VERSION},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
