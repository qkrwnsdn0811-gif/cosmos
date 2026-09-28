"""Period/source-specific relationship evidence support, independent of GNN impact.

rel-v0.3-components uses the SAME bounded support formula for NEWS and DISCLOSURE:
100 * (1 - exp(-sum(per-document confidence) / 5)). Five confidence-1 documents
score 63.212056. This is a transparent display heuristic, not a calibrated probability
or measured price impact. Ownership percentage is NOT comparable to article counts,
so it is not used as the disclosure score. Sector signals are not mixed in.
Pair price correlation adds at most PRICE_BONUS_MAX points on top (see price_bonus).

Windows are [as_of - N calendar days, as_of), UTC — 30D / 1Y / 10Y (see WINDOWS). Date-only evidence is available at
the END of that UTC date; no accounting date is substituted for DART receipt date.
Old disclosures expire from a window. Persistent ownership is a different policy.
Each (relationship type, oriented pair, source, document ID) contributes once, using
the maximum sentence confidence. PARTNER/COMPETE are canonicalized; SUPPLY/INVEST
remain directed. Components are NULL only when no evidence exists (zero confidence
is a real zero). Missing both sources omits the row; one source uses its own score.

Input must cover the full 90-day extraction, not the local rel_hits inspection sample.
Malformed evidence fails before writing output; unknown companies are reported and
skipped. Separate exports are required for each completed batch, including empty files
when extraction succeeded with no hits. Missing files are errors, not empty evidence.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import re
from collections import defaultdict
from pathlib import Path

UTC = dt.timezone.utc
# 창 — 2026-09-22 사용자 결정으로 7D/30D/90D 에서 30D/1Y/10Y 로 바꿨다.
#
# 왜: 관계 근거의 91%가 1년보다 오래됐다(실측 10,354건 중 9,432건). 90일 창으로는
# 추출한 관계 1,200개 중 83개만 점수 행이 생겼다. 원천 뉴스가 과거 아카이브 위주고
# 2025~2026 수집이 얇아서다.
#
#   30일 이내   258건   고유관계   65
#   90일 이내    32건            25
#   1년 이내    632건           194
#   1년 초과  9,432건         1,088
#
# (라벨, 일수) 로 둔다 — 3650 을 "3650D" 로 쓰면 화면에서 읽히지 않는다.
WINDOWS = (("30D", 30), ("1Y", 365), ("10Y", 3650))
MAX_WINDOW_DAYS = max(d for _, d in WINDOWS)
# 전수 재처리판. 창(30D/1Y/10Y)·주가 가산점·INVEST 지분율이 모두 바뀌었으므로
# 예전 rel-v0.3-components 행과 섞이지 않게 판을 올린다 (되돌릴 때 이 값으로 고른다).
FORMULA_VERSION = "rel-v0.5-full"
TYPES = {"SUPPLY", "INVEST", "PARTNER", "COMPETE"}
NATURAL_FIELDS = ["source_market", "source_stock_code", "target_market",
                  "target_stock_code", "relationship_type_code"]
SCORE_FIELDS = NATURAL_FIELDS + ["window_type", "news_score", "disclosure_score", "score",
    "impact_direction", "confidence", "evidence_count", "formula_version", "as_of_at"]


def timestamp(value: str, *, evidence: bool = False) -> dt.datetime:
    value = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        result = dt.datetime.combine(dt.date.fromisoformat(value), dt.time(), UTC)
        # Availability is represented by the final microsecond of the date.
        return result + dt.timedelta(days=1, microseconds=-1) if evidence else result
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("시간을 지정할 때는 UTC offset이 필요합니다")
    return result.astimezone(UTC)


def json_rows(path: Path):
    if not path.exists():
        raise FileNotFoundError(path)
    files = sorted(path.glob("part-*")) if path.is_dir() else [path]
    if path.is_dir() and not (path / "_SUCCESS").exists():
        raise ValueError(f"완료되지 않은 Spark export: {path}")
    for file in files:
        if not file.is_file():
            continue
        with file.open(encoding="utf-8-sig") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def normalize(row: dict, source: str) -> dict:
    if source not in {"NEWS", "DISCLOSURE"}:
        raise ValueError("지원하지 않는 근거 출처")
    for field in ("src_ticker", "dst_ticker", "record_id"):
        if not isinstance(row[field], str) or not row[field].strip():
            raise ValueError(f"{field}는 비어 있지 않은 문자열이어야 합니다")
    src, dst = str(row["src_ticker"]).strip(), str(row["dst_ticker"]).strip()
    rel = row["rel_type"]
    if rel not in TYPES or not src or not dst or src == dst:
        raise ValueError(f"잘못된 관계: {src}/{dst}/{rel}")
    if rel in {"PARTNER", "COMPETE"}:
        src, dst = sorted((src, dst))
    document = str(row["record_id"]).strip()
    if not document:
        raise ValueError("빈 근거 ID")
    confidence = float(row["confidence"])
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence는 0~1 유한수여야 합니다")
    return {"key": (src, dst, rel), "source": source, "document": document,
            "at": timestamp(row["published_date"], evidence=True), "confidence": confidence}


def ownership_evidence(path: Path):
    """Adapt current ownership edges. rcept_no is publication; as_of_date is accounting.

    An explicit disclosure JSONL is preferable when historical filings are available.
    The latest-only ownership CSV cannot reconstruct past filing histories.
    """
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            receipt = re.search(r"(?:^|;)dart:(\d{14})(?:;|$)", row["evidence"])
            if not receipt:
                raise ValueError("DART 접수번호 없는 지분 근거: 날짜를 임의 추정하지 않습니다")
            rid = receipt[1]
            day = dt.datetime.strptime(rid[:8], "%Y%m%d").date().isoformat()
            yield {"src_ticker": row["src_ticker"], "dst_ticker": row["dst_ticker"],
                   "rel_type": "INVEST", "record_id": rid, "published_date": day,
                   "confidence": 1.0}


# ── 미적용 결정 (2026-09-22, 사용자 합의) ────────────────────────────────
# 전수 재처리 때 INVEST 를 지분율로 매긴다. 지금은 근거 **개수**로 매겨서
# 지분 79.38%(LG화학->LG엔솔) 가 신고 1건이라 18.1점이고, 5% 턱걸이인데 신고를
# 세 번 한 쪽이 45.1점이다. 신고 횟수가 지배력처럼 보인다.
#
#   INVEST                    -> 지분율 (edges_ownership.csv 의 weight, 실측 44행)
#   SUPPLY/PARTNER/COMPETE    -> 근거 개수 (강도를 잴 지표가 없다)
#
# 머리말의 "Ownership percentage is NOT comparable to article counts" 는 여전히
# 맞다 — 같은 100점 눈금에 다른 척도가 섞인다. 그래도 지분율을 통째로 버리는
# 지금보다 낫다고 판단했다. 화면에 근거를 "지분 79.38%" 로 같이 띄워 완화한다.
#
# 창(window) 정책도 같이 고친다. INVEST 는 신고일 기준으로 만료시키면 안 된다 —
# 해지 신고 전까지 유효한 **상태**다. 실측: 근거 1,530건 중 최근 7일 0건,
# 30일 11건이라 지금 정책으로는 7D 에서 INVEST 가 통째로 사라진다.


def component(documents: list[dict]) -> float | None:
    if not documents:
        return None
    return round(100 * -math.expm1(-math.fsum(d["confidence"] for d in documents) / 5), 6)


# 주가 동조 가산점 (2026-09-22, 사용자 요청)
# 관계 점수는 "근거가 얼마나 쌓였나" 다. 주가 상관은 그 증거가 아니다 — 같은 업종이면
# 관계가 없어도 같이 움직인다(같은 업종 730쌍의 평균 43.7%가 한쪽 오르고 한쪽 내림).
# 그래서 **근거 순위를 뒤집지 못하는 크기**로만 얹는다.
#
#   최대 +3점. 실측 상관은 0.303~0.994(중앙 0.420) 이라 실제 가산은 1~3점이다.
#   근거 1건(18.1)과 2건(33.0)의 간격이 15점이므로 근거 순위를 넘지 못한다.
#
# 누설 주의: edges_correlation.csv 의 창은 2025-09-19~2026-09-11 이다. as_of 가 그 창
# 안이면 미래 가격으로 만든 값이 점수에 들어간다. 예전 build_relationship_seed.py 가
# 이 때문에 지적됐다(317관계 중 21개에서 보강항이 max, rank-IC 0.0298->0.0297).
# **창 종료일이 as_of 이전인 상관만 넘길 것.** 그래서 호출 측이 값을 주게 했다.
PRICE_BONUS_MAX = 3.0


def price_bonus(corr):
    """|상관| 에 비례한 가산점. 없으면 0. 이 항만으로 점수가 생기지는 않는다."""
    if corr is None:
        return 0.0
    try:
        c = float(corr)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(c):
        return 0.0
    return round(PRICE_BONUS_MAX * min(abs(c), 1.0), 6)


# INVEST 는 지분율로 매긴다 (2026-09-22 사용자 합의).
#
# 근거 개수로 매기면 지분 79.38%(LG화학->LG엔솔)가 신고 1건이라 18.1점인데, 5% 턱걸이를
# 세 번 신고한 쪽이 45.1점이 된다. 신고 횟수가 지배력처럼 보인다.
#
# 창(30D/1Y/10Y)도 적용하지 않는다. 지분은 신고일의 사건이 아니라 해지 신고 전까지
# 유효한 **상태**다. 실측: 근거 1,530건 중 최근 30일이 11건뿐이라 창을 걸면 INVEST 가
# 거의 사라진다.
#
# 눈금: 5% 가 법정 신고 기준선이고 25% 부터 실질 지배력으로 본다. 50% 면 확정적이다.
#   5% -> 20점 · 25% -> 60점 · 50% -> 85점 · 79% -> 97점
def invest_score(pct: float) -> float:
    """지분율(0~1)을 0~100 으로. 5% 미만은 신고 대상이 아니므로 0."""
    if pct is None or pct < 0.05:
        return 0.0
    return round(min(100.0, 100 * (1 - math.exp(-float(pct) / 0.28))), 6)


def blend(news: float | None, disclosure: float | None) -> float | None:
    if news is None:
        return disclosure
    if disclosure is None:
        return news
    return round((news + disclosure) / 2, 6)


def aggregate(evidence, markets: dict, as_of: dt.datetime, overrides=None,
              price_corr=None):
    """price_corr: {(정렬된 티커쌍): 상관} — 창 종료일이 as_of 이전인 것만 넘긴다."""
    docs = {}
    unknown = set()
    for item in evidence:
        src, dst, rel = item["key"]
        if src not in markets or dst not in markets:
            unknown.add(item["key"])
            continue
        if not as_of - dt.timedelta(days=MAX_WINDOW_DAYS) <= item["at"] < as_of:
            continue
        key = (*item["key"], item["source"], item["document"])
        previous = docs.get(key)
        if previous and previous["at"] != item["at"]:
            raise ValueError(f"동일 문서의 발행 시각 불일치: {key}")
        if previous is None or previous["confidence"] < item["confidence"]:
            docs[key] = item
    groups = defaultdict(list)
    for item in docs.values():
        if as_of - dt.timedelta(days=MAX_WINDOW_DAYS) <= item["at"] < as_of:
            groups[item["key"]].append(item)
    rows = []
    for (src, dst, rel), items in sorted(groups.items()):
        for label, days in WINDOWS:
            present = [d for d in items if d["at"] >= as_of - dt.timedelta(days=days)]
            news = component([d for d in present if d["source"] == "NEWS"])
            disclosure = component([d for d in present if d["source"] == "DISCLOSURE"])
            score = blend(news, disclosure)
            if score is None:
                continue
            # 근거가 있는 관계에만 얹는다. 이 항만으로 관계가 생기지는 않는다.
            bonus = price_bonus((price_corr or {}).get(tuple(sorted((src, dst)))))
            score = round(min(100.0, score + bonus), 6)
            rows.append(dict(zip(NATURAL_FIELDS, [markets[src], src, markets[dst], dst, rel])) | {
                "window_type": label, "news_score": news, "disclosure_score": disclosure,
                "score": score, "impact_direction": (overrides or {}).get((tuple(sorted((src, dst))), rel)),
                "confidence": round(math.fsum(d["confidence"] for d in present) / len(present), 6),
                "evidence_count": len(present), "formula_version": FORMULA_VERSION,
                "as_of_at": as_of.isoformat().replace("+00:00", "Z")})
    return rows, sorted(unknown)


def build_seed(args, out: Path):
    from itertools import chain
    from build_relationship_seed import read_csv, RELATIONSHIP_TYPES, MODEL_VERSION, HDFS_URI
    as_of = timestamp(args.as_of)
    markets = {r["ticker"]: r["market"] for r in read_csv(args.companies)}
    if not markets:
        raise ValueError("기업 사전이 비어 있습니다")
    overrides = {(tuple(sorted((r["source_stock_code"], r["target_stock_code"]))),
                  r["relationship_type_code"]): r["impact_direction"]
                 for r in read_csv(args.impact_override)}
    disclosure = (json_rows(args.disclosure_evidence) if args.disclosure_evidence
                  else ownership_evidence(args.ownership))
    evidence = chain((normalize(r, "NEWS") for r in json_rows(args.news_hits)),
                     (normalize(r, "DISCLOSURE") for r in disclosure))
    rows, unknown = aggregate(evidence, markets, as_of, overrides)
    relationships = {tuple(r[f] for f in NATURAL_FIELDS): {f: r[f] for f in NATURAL_FIELDS} for r in rows}
    out.mkdir(parents=True, exist_ok=True)
    def write(name, fields, values):
        with (out / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)  # None -> unquoted empty field -> PostgreSQL NULL
    stamp = as_of.isoformat().replace("+00:00", "Z")
    write("relationship_type.csv", ["code", "name", "directionality"],
          [dict(zip(("code", "name", "directionality"), r)) for r in RELATIONSHIP_TYPES])
    write("graph_snapshot.csv", ["as_of_at", "formula_version", "model_version", "status", "hdfs_uri", "published_at"],
          [{"as_of_at": stamp, "formula_version": FORMULA_VERSION, "model_version": MODEL_VERSION,
            "status": "PUBLISHED", "hdfs_uri": HDFS_URI, "published_at": stamp}])
    write("company_relationship.csv", NATURAL_FIELDS, relationships.values())
    write("relationship_score_current.csv", SCORE_FIELDS, rows)
    loader = Path(__file__).with_name("load_relationship_components.sql")
    (out / "load_relationships.sql").write_text(loader.read_text(encoding="utf-8"), encoding="utf-8")
    summary = {"formula_version": FORMULA_VERSION, "as_of_at": stamp, "rows": len(rows),
               "by_window": {lab: sum(r["window_type"] == lab for r in rows) for lab, _ in WINDOWS},
               "unknown_relationships": unknown, "disclosure_policy": "receipt_in_window",
               "news_input": str(args.news_hits),
               "disclosure_input": str(args.disclosure_evidence or args.ownership)}
    (out / "component_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
