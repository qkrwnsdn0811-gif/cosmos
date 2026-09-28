"""Step 6 — 근거 문장에 FinBERT 감성을 붙인다 (로컬 GPU).

build_evidence_sentences.py 가 낸 파케이를 먹고, 기업별 감성을 낸다.
infer_missing_sentiment.py 의 Classifier·가드를 그대로 재사용한다 — 같은 판정 규칙을
두 번 구현하지 않는다.

  # 1) 서버에서 내려받기 (문장 텍스트만, 원문 22.5GB 아님)
  hdfs dfs -get <evidence-sentences run>/data  data/evidence_sentences

  # 2) 로컬 GPU
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe score_evidence_sentences.py \
      --src data/evidence_sentences --device cuda --out data/sentiment_out.parquet

  # 시험
  ... score_evidence_sentences.py --src ... --limit 5000

════════════════════════════════════════════════════════════════════════════
선별을 먼저 건다 — 버릴 것에 GPU 를 쓰지 않는다
════════════════════════════════════════════════════════════════════════════
--mentions 로 company-mentions 의 data/ 를 같이 주면 아래 규칙으로 먼저 거른다.
표본 192건에서 정밀도 75% -> 93% 였고 절반 이상이 걸러졌다.

    confidence < 1.0                          이름 충돌 ("한화로 3조원" -> 한화)
    기사 내 기업 수 >= 10                       시황·나열
    n_mentions <= 1 and first_pos != "title"   스쳐 지나감

이 표본은 B 분류기가 include 한 것만 모은 것이라 **정밀도만** 잰 값이다. 재현율은
측정하지 않았다.

════════════════════════════════════════════════════════════════════════════
판정 규칙 (infer_missing_sentiment.py 와 동일)
════════════════════════════════════════════════════════════════════════════
- 문장에 대상 기업명이 없으면 버린다 (_surface — 문장부호 안 지움, 영문 경계만 확인)
- 같은 기사에 붙은 **다른 기업**이 그 문장에 있으면 버린다 (전체 유니버스가 아니다)
- 한 기업에 대해 문장마다 라벨이 갈리면 NULL. 다수결로 밀지 않는다
- 한글이 있으면 KR-FinBert-SC, 없으면 ProsusAI/finbert
- 라벨 순서는 config.json 의 id2label 로 매핑한다 (두 모델이 반대다)

직접 만든 가드는 통과율이 2.5% 까지 떨어졌었다. 공백을 지우는 바람에 '삼성' 이
'삼성전자' 안에서 매칭됐다. 기존 함수를 쓰면 92.6% 다.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from infer_missing_sentiment import Classifier, HANGUL, load_alias_by_name

HERE = Path(__file__).resolve().parent
MODEL_VERSION = "evidence-sentence-finbert-v1"
MODELS = {"ko": "sentiment_ko", "en": "sentiment_en"}


def load_frames(src: Path, mentions: Path | None, limit: int):
    import pandas as pd

    df = pd.read_parquet(src)
    need = {"record_id", "ticker", "sentence_order", "sentence_text"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"입력에 없는 컬럼: {sorted(missing)}")

    if mentions is not None:
        m = pd.read_parquet(mentions, columns=["record_id", "ticker", "n_mentions",
                                               "first_pos", "confidence"])
        n_comp = m.groupby("record_id")["ticker"].nunique().rename("n_comp")
        m = m.join(n_comp, on="record_id")
        drop = ((m["confidence"] < 1.0)
                | (m["n_comp"] >= 10)
                | ((m["n_mentions"] <= 1) & (m["first_pos"] != "title")))
        keep = m.loc[~drop, ["record_id", "ticker"]]
        before = len(df)
        df = df.merge(keep, on=["record_id", "ticker"], how="inner")
        print(f"선별: 문장 {before:,} -> {len(df):,} ({len(df)*100//max(before,1)}%)")

    if limit:
        df = df.head(limit)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True, help="evidence-sentences 파케이 디렉터리")
    ap.add_argument("--mentions", type=Path, default=None, help="company-mentions 의 data/ (선별용)")
    ap.add_argument("--out", type=Path, default=HERE / "data" / "sentiment_out.parquet")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ticker-names", type=Path,
                    default=HERE.parent / "ner" / "data" / "companies.csv")
    args = ap.parse_args()

    import csv as _csv
    import pandas as pd
    from infer_missing_sentiment import _surface

    df = load_frames(args.src, args.mentions, args.limit)
    if df.empty:
        raise SystemExit("입력이 비었다")

    with args.ticker_names.open(encoding="utf-8-sig", newline="") as f:
        name_of = {r["ticker"]: (r.get("name_official") or "").strip()
                   for r in _csv.DictReader(f)}
    alias_by_name = load_alias_by_name()

    def names_of(ticker: str) -> set[str]:
        official = name_of.get(ticker, "")
        return alias_by_name.get(official, {official} if official else set())

    # 기사별 등장 기업 — "다른 기업이 그 문장에 있으면 버린다" 판정에 쓴다.
    # 전체 유니버스가 아니라 **그 기사에 붙은** 기업만 본다.
    in_doc = df.groupby("record_id")["ticker"].apply(set).to_dict()

    kept, drops = [], collections.Counter()
    for row in df.itertuples(index=False):
        text = row.sentence_text or ""
        if not any(_surface(n, text) for n in names_of(row.ticker)):
            drops["문장에 대상 기업명 없음"] += 1
            continue
        others = in_doc.get(row.record_id, set()) - {row.ticker}
        if any(_surface(n, text) for o in others for n in names_of(o)):
            drops["같은 기사의 다른 기업이 문장에 있음"] += 1
            continue
        kept.append(row)
    print(f"가드 통과 {len(kept):,} / {len(df):,} ({len(kept)*100//max(len(df),1)}%)")
    for k, v in drops.most_common():
        print(f"  버림 {k}: {v:,}")
    if not kept:
        raise SystemExit("가드를 통과한 문장이 없다")

    ko = [r for r in kept if HANGUL.search(r.sentence_text or "")]
    en = [r for r in kept if not HANGUL.search(r.sentence_text or "")]
    print(f"한국어 {len(ko):,} · 영문 {len(en):,}")

    labels: dict[tuple, list[str]] = collections.defaultdict(list)
    for rows, key in ((ko, "ko"), (en, "en")):
        if not rows:
            continue
        clf = Classifier(HERE / "local_models" / MODELS[key], args.device)
        got = clf.predict([r.sentence_text for r in rows])
        for r, lab in zip(rows, got):
            labels[(r.record_id, r.ticker)].append(lab)
        del clf

    # 만장일치만 인정한다. 갈리면 NULL — 다수결로 밀지 않는다.
    out, conflict = [], 0
    for (rec, ticker), labs in labels.items():
        uniq = set(labs)
        if len(uniq) > 1:
            conflict += 1
            continue
        out.append({"record_id": rec, "ticker": ticker, "sentiment": uniq.pop(),
                    "n_sentences": len(labs), "model_version": MODEL_VERSION})
    print(f"라벨 확정 {len(out):,} · 라벨 갈려 NULL {conflict:,}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(out).to_parquet(args.out, index=False)
    dist = collections.Counter(o["sentiment"] for o in out)
    print(json.dumps({"out": str(args.out), "rows": len(out),
                      "distribution": dict(dist), "model_version": MODEL_VERSION},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
