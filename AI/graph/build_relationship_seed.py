"""Knowledge graph step 7 — DB seed for the relationship tables, from every typed edge we have.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_relationship_seed.py
  -> data/db/{relationship_type,graph_snapshot,company_relationship,relationship_score_current}.csv
  -> data/db/load_relationships.sql

유형(type)과 세기(score)는 서로 다른 데서 온다
---------------------------------------------
프론트(`FrontEnd/src/lib/meta.ts`)와 API 타입이 관계 유형을 SUPPLY·INVEST·PARTNER·COMPETE
4종으로 고정해 두었고, `RelationMap2D` 는 이 4종만 렌더링한다. 우리 엣지를 여기 대보면
역할이 둘로 갈린다.

  유형을 정하는 엣지
    edges_ownership          -> INVEST                      (DART 공시, 방향 있음)
    edges_relation_scored    -> SUPPLY / PARTNER / COMPETE   (문장 관계추출)

  유형은 못 정하지만 세기를 보강하는 엣지
    edges_co_mention         같이 언급되는 정도 (npmi)
    edges_correlation        주가 잔차 상관
    edges_sector             같은 업종인지

"같이 언급된다"·"같이 움직인다"·"같은 업종이다"는 *무슨* 관계인지 말해주지 않는다. 그래서
유형으로 쓰지 않고, 유형이 이미 정해진 관계가 **얼마나 실체가 있는지**를 보강하는 데 쓴다.
스키마가 score·confidence·evidence_count 를 따로 둔 것도 이 구조를 전제한다.

    score (0~100)     관계의 세기. 자기 엣지의 weight 를 주로 쓰고, 보강 신호를 25% 섞는다.
    confidence (0~1)  그 판단을 얼마나 믿는가. 공시=1.0, 문장 추출=패턴 신뢰도.
    evidence_count    근거 기사 수. 지분은 문서 근거가 없으므로 0.

적재 필수조건 3가지 (BackEnd GraphService / GraphQueryRepository)
---------------------------------------------------------------
    GRAPH_WINDOW = "30D"          -> window_type 은 반드시 '30D'
    graph_snapshot.status         = 'PUBLISHED' 인 것 중 as_of_at 최신 **1건만** 씀
    company.status                = 'ACTIVE' 인 회사만 엣지에 나옴
셋 중 하나라도 어긋나면 API가 빈 그래프를 돌려준다. 특히 스냅샷이 하나만 쓰이므로
**모든 엣지를 한 스냅샷에 넣어야 한다** — 지분만 따로 오래된 as_of 로 넣으면 통째로 묻힌다.

relationship_evidence 는 만들지 않는다: document_id 가 NOT NULL 인데 우리 근거는 HDFS
record_id 라서 source_document 의 UUID 로 매핑되기 전에는 행을 만들 수 없다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
COMPANIES = HERE.parent / "ner" / "data" / "companies.csv"
OUT = DATA / "db"

FORMULA_VERSION = "rel-v0.2-typed"
MODEL_VERSION = "dict-v1.3"
WINDOW_TYPE = "30D"          # BackEnd GraphService.GRAPH_WINDOW
HDFS_URI = "hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-mentions/model_version=dict-v1.3"

RELATIONSHIP_TYPES = [
    ("SUPPLY", "공급", "DIRECTED"),      # A가 B에 납품
    ("INVEST", "투자", "DIRECTED"),      # A가 B 지분 보유
    ("PARTNER", "협력", "UNDIRECTED"),
    ("COMPETE", "경쟁", "UNDIRECTED"),
]
# 유형만 보고 주는 기본 부호. COMPETE·PARTNER 는 이것만으로는 틀린다 (아래 참고).
IMPACT = {"SUPPLY": "POSITIVE", "INVEST": "POSITIVE", "PARTNER": "POSITIVE", "COMPETE": "NEGATIVE"}
# estimate_edge_direction.py 가 쌍별 가격 반응으로 추정한 부호. 있으면 그쪽을 쓴다.
# "COMPETE 니까 NEGATIVE" 는 test 구간 적중률이 5% 다 — 국내 동종업계는 같은 산업 충격을
# 같이 맞아 같이 움직인다 (삼성전자-SK하이닉스, NAVER-카카오, 신한지주-KB금융).
# 기준선은 유형 기본값이 아니라 **파라미터 0개인 "전부 POSITIVE"** 로 잡아야 한다. test
# 구간 부호 적중률 63.5%(전부POS) -> 71.9%(쌍별). 근거·한계는 그쪽 docstring 참고.
IMPACT_OVERRIDE = DATA / "db" / "relationship_impact_direction.csv"
CORROBORATION_SHARE = 0.25   # 점수에서 보강 신호가 차지하는 몫
SECTOR_SIGNAL = 0.3          # 같은 업종은 약한 근거라 고정 소액만 인정


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def pair(a: str, b: str) -> tuple:
    return tuple(sorted((a, b)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ownership", default=DATA / "edges_ownership.csv", type=Path)
    ap.add_argument("--relations", default=DATA / "edges_relation_scored.csv", type=Path)
    ap.add_argument("--co-mention", default=DATA / "edges_co_mention.csv", type=Path)
    ap.add_argument("--correlation", default=DATA / "edges_correlation.csv", type=Path)
    ap.add_argument("--sector", default=DATA / "edges_sector.csv", type=Path)
    ap.add_argument("--companies", default=COMPANIES, type=Path)
    ap.add_argument("--out", default=OUT, type=Path)
    ap.add_argument("--as-of", default=dt.date.today().isoformat())
    ap.add_argument("--impact-override", default=IMPACT_OVERRIDE, type=Path,
                    help="쌍별 impact_direction 표. 없으면 유형 기본값만 쓴다")
    args = ap.parse_args()

    market = {r["ticker"]: r["market"] for r in read_csv(args.companies)}
    as_of_at = f"{args.as_of}T00:00:00Z"

    # 쌍별 부호. 키는 (무순서 쌍, 유형) — 무방향 유형이 양쪽 행으로 들어와도 같은 값을 준다.
    impact_by_pair: dict[tuple, str] = {}
    for r in read_csv(args.impact_override):
        k = (pair(r["source_stock_code"], r["target_stock_code"]), r["relationship_type_code"])
        impact_by_pair[k] = r["impact_direction"]

    # ---- 보강 신호 (유형은 못 정하지만 세기를 받쳐준다)
    co = {pair(r["src_ticker"], r["dst_ticker"]): float(r["weight"]) for r in read_csv(args.co_mention)}
    corr = {pair(r["src_ticker"], r["dst_ticker"]): float(r["weight"]) for r in read_csv(args.correlation)}
    sect = {pair(r["src_ticker"], r["dst_ticker"]) for r in read_csv(args.sector)}

    def corroboration(a: str, b: str) -> float:
        p = pair(a, b)
        return max(co.get(p, 0.0), corr.get(p, 0.0), SECTOR_SIGNAL if p in sect else 0.0)

    # 주의(모델 평가용으로 score 를 쓸 때): corr 은 edges_correlation.csv 에서 오는데 그 파일은
    # 2025-09~2026-09 가격 창으로 만들어졌다. 스토리 3 의 test 구간(2024-01~) 안이다. 317관계
    # 중 21개에서 이 항이 실제로 max 를 차지한다. 화면용 세기로는 문제가 없지만, score 를
    # 예측 모델의 특징으로 넣으면 그 21개에 미래정보가 실린다. 실측 영향은 관계 베이스라인
    # rank-IC 0.0298 -> 0.0297 (KOSPI), NASDAQ 은 변화 없음 — 무시할 크기지만 알고 쓸 것.

    # ---- 유형이 정해진 엣지들
    typed: list[dict] = []
    for r in read_csv(args.ownership):
        typed.append({"src": r["src_ticker"], "dst": r["dst_ticker"], "type": "INVEST",
                      "base": float(r["weight"]), "conf": 1.0, "n_docs": 0})
    for r in read_csv(args.relations):
        typed.append({"src": r["src_ticker"], "dst": r["dst_ticker"], "type": r["rel_type"],
                      "base": float(r["weight"]), "conf": float(r.get("mean_conf") or 0.6),
                      "n_docs": int(r.get("n_docs") or 0)})

    rels, scores, skipped = [], [], []
    seen: set[tuple] = set()
    for e in typed:
        src, dst = e["src"], e["dst"]
        if src not in market or dst not in market:
            skipped.append((src, dst, e["type"], "유니버스에 없는 종목"))
            continue
        if src == dst:
            skipped.append((src, dst, e["type"], "자기 자신"))          # chk_..._distinct_companies
            continue
        key = (market[src], src, market[dst], dst, e["type"])
        if key in seen:
            skipped.append((src, dst, e["type"], "중복"))               # uk_company_relationship_identity
            continue
        seen.add(key)
        nat = {"source_market": market[src], "source_stock_code": src,
               "target_market": market[dst], "target_stock_code": dst,
               "relationship_type_code": e["type"]}
        rels.append(nat)
        score = 100.0 * ((1 - CORROBORATION_SHARE) * e["base"]
                         + CORROBORATION_SHARE * corroboration(src, dst))
        scores.append({**nat, "window_type": WINDOW_TYPE, "score": round(score, 6),
                       "impact_direction": impact_by_pair.get((pair(src, dst), e["type"]),
                                                              IMPACT[e["type"]]),
                       "confidence": round(min(1.0, e["conf"]), 6),
                       "evidence_count": e["n_docs"],
                       "formula_version": FORMULA_VERSION, "as_of_at": as_of_at})

    args.out.mkdir(parents=True, exist_ok=True)
    def write(name: str, fields: list[str], rows: list[dict]) -> None:
        with (args.out / name).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"  {name:34} {len(rows):>4}행")

    from collections import Counter
    by = Counter(r["relationship_type_code"] for r in rels)
    print(f"유형 있는 엣지 {len(typed)} -> 관계 {len(rels)}   {dict(by)}")
    write("relationship_type.csv", ["code", "name", "directionality"],
          [{"code": c, "name": n, "directionality": d} for c, n, d in RELATIONSHIP_TYPES])
    write("graph_snapshot.csv",
          ["as_of_at", "formula_version", "model_version", "status", "hdfs_uri", "published_at"],
          [{"as_of_at": as_of_at, "formula_version": FORMULA_VERSION, "model_version": MODEL_VERSION,
            "status": "PUBLISHED", "hdfs_uri": HDFS_URI, "published_at": as_of_at}])
    write("company_relationship.csv",
          ["source_market", "source_stock_code", "target_market", "target_stock_code",
           "relationship_type_code"], rels)
    write("relationship_score_current.csv",
          ["source_market", "source_stock_code", "target_market", "target_stock_code",
           "relationship_type_code", "window_type", "score", "impact_direction",
           "confidence", "evidence_count", "formula_version", "as_of_at"], scores)
    (args.out / "load_relationships.sql").write_text(LOADER_SQL, encoding="utf-8")
    print(f"  {'load_relationships.sql':34}")
    if skipped:
        print(f"제외 {len(skipped)}건:", Counter(s[3] for s in skipped))
    s = [x["score"] for x in scores]
    print(f"\nscore {min(s):.1f}~{max(s):.1f} | window={WINDOW_TYPE} | snapshot PUBLISHED as_of={as_of_at}")
    print("적재: docker exec -w <csv폴더> -i cosmos-postgres psql -U cosmos -d cosmos -f load_relationships.sql")


LOADER_SQL = r"""-- 관계 시드 적재. 자연키를 UUID로 해석한다. 여러 번 돌려도 안전하다(ON CONFLICT).
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f load_relationships.sql
-- CSV 4개가 이 파일과 같은 디렉터리에 있어야 한다(\copy 는 클라이언트 경로 기준).
BEGIN;

CREATE TEMP TABLE _rel_type (code text, name text, directionality text) ON COMMIT DROP;
CREATE TEMP TABLE _snapshot (as_of_at timestamptz, formula_version text, model_version text,
                             status text, hdfs_uri text, published_at timestamptz) ON COMMIT DROP;
CREATE TEMP TABLE _rel (source_market text, source_stock_code text, target_market text,
                        target_stock_code text, relationship_type_code text) ON COMMIT DROP;
CREATE TEMP TABLE _score (source_market text, source_stock_code text, target_market text,
                          target_stock_code text, relationship_type_code text, window_type text,
                          score numeric, impact_direction text, confidence numeric,
                          evidence_count int, formula_version text, as_of_at timestamptz) ON COMMIT DROP;

\copy _rel_type     FROM 'relationship_type.csv'          WITH (FORMAT csv, HEADER true)
\copy _snapshot     FROM 'graph_snapshot.csv'             WITH (FORMAT csv, HEADER true)
\copy _rel          FROM 'company_relationship.csv'       WITH (FORMAT csv, HEADER true)
\copy _score        FROM 'relationship_score_current.csv' WITH (FORMAT csv, HEADER true)

-- 1) 관계 유형 4종
INSERT INTO relationship_type (code, name, directionality)
SELECT code, name, directionality FROM _rel_type
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name,
                                 directionality = EXCLUDED.directionality;

-- 2) 그래프 스냅샷. 같은 (as_of_at, formula_version) 이면 재사용한다.
INSERT INTO graph_snapshot (as_of_at, formula_version, model_version, status, hdfs_uri, published_at)
SELECT s.as_of_at, s.formula_version, s.model_version, s.status, s.hdfs_uri, s.published_at
FROM _snapshot s
WHERE NOT EXISTS (SELECT 1 FROM graph_snapshot g
                  WHERE g.as_of_at = s.as_of_at AND g.formula_version = s.formula_version);

-- 3) 기업 관계
INSERT INTO company_relationship (source_company_id, target_company_id, relationship_type_id)
SELECT sc.company_id, tc.company_id, rt.relationship_type_id
FROM _rel r
JOIN company sc ON sc.market = r.source_market AND sc.stock_code = r.source_stock_code
JOIN company tc ON tc.market = r.target_market AND tc.stock_code = r.target_stock_code
JOIN relationship_type rt ON rt.code = r.relationship_type_code
ON CONFLICT (source_company_id, target_company_id, relationship_type_id) DO NOTHING;

-- 4) 현재 점수
INSERT INTO relationship_score_current (relationship_id, window_type, snapshot_id, score,
                                        impact_direction, confidence, evidence_count,
                                        formula_version, as_of_at)
SELECT cr.relationship_id, s.window_type, g.snapshot_id, s.score,
       NULLIF(s.impact_direction, ''), s.confidence, s.evidence_count, s.formula_version, s.as_of_at
FROM _score s
JOIN company sc ON sc.market = s.source_market AND sc.stock_code = s.source_stock_code
JOIN company tc ON tc.market = s.target_market AND tc.stock_code = s.target_stock_code
JOIN relationship_type rt ON rt.code = s.relationship_type_code
JOIN company_relationship cr ON cr.source_company_id = sc.company_id
                            AND cr.target_company_id = tc.company_id
                            AND cr.relationship_type_id = rt.relationship_type_id
JOIN graph_snapshot g ON g.as_of_at = s.as_of_at AND g.formula_version = s.formula_version
ON CONFLICT (relationship_id, window_type)
DO UPDATE SET snapshot_id = EXCLUDED.snapshot_id, score = EXCLUDED.score,
              impact_direction = EXCLUDED.impact_direction, confidence = EXCLUDED.confidence,
              evidence_count = EXCLUDED.evidence_count, formula_version = EXCLUDED.formula_version,
              as_of_at = EXCLUDED.as_of_at;

-- 5) 이번 발행에 없는 이전 점수 행 정리.
-- 같은 스냅샷(as_of_at + formula_version)으로 다시 발행하면 INSERT/UPDATE 만으로는 지난번에
-- 있었다가 이번에 사라진 관계의 점수 행이 그대로 남는다. 그 행들이 최신 스냅샷을 가리키고
-- 있으므로 API 그래프에 유령 엣지로 뜬다 — 문장 분리 수정 후 재발행에서 실제로 10건 남았다.
-- company_relationship 행 자체는 남겨둔다(이력이고, 점수 행이 없으면 그래프 쿼리에 안 잡힌다).
DELETE FROM relationship_score_current rsc
USING graph_snapshot g
WHERE rsc.snapshot_id = g.snapshot_id
  AND (g.as_of_at, g.formula_version) IN (SELECT as_of_at, formula_version FROM _snapshot)
  AND NOT EXISTS (
      SELECT 1 FROM _score s
      JOIN company sc ON sc.market = s.source_market AND sc.stock_code = s.source_stock_code
      JOIN company tc ON tc.market = s.target_market AND tc.stock_code = s.target_stock_code
      JOIN relationship_type rt ON rt.code = s.relationship_type_code
      JOIN company_relationship cr ON cr.source_company_id = sc.company_id
                                  AND cr.target_company_id = tc.company_id
                                  AND cr.relationship_type_id = rt.relationship_type_id
      WHERE cr.relationship_id = rsc.relationship_id
        AND s.window_type = rsc.window_type);

COMMIT;

-- 확인용: API가 실제로 보게 될 엣지 수 (company.status='ACTIVE' 조건 포함)
--   SELECT rt.code, count(*) FROM relationship_score_current rsc
--     JOIN company_relationship cr ON cr.relationship_id = rsc.relationship_id
--     JOIN relationship_type rt ON rt.relationship_type_id = cr.relationship_type_id
--     JOIN company sc ON sc.company_id = cr.source_company_id AND sc.status = 'ACTIVE'
--     JOIN company tc ON tc.company_id = cr.target_company_id AND tc.status = 'ACTIVE'
--     JOIN graph_snapshot g ON g.snapshot_id = rsc.snapshot_id AND g.status = 'PUBLISHED'
--    WHERE rsc.window_type = '30D' GROUP BY rt.code;
"""


if __name__ == "__main__":
    main()
