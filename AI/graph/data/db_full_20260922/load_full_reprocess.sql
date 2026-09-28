-- 전수 재처리 결과(rel-v0.5-full)를 운영 public 스키마에 적재한다.
--
--   docker exec -w /tmp/load -i cosmos-postgres-1 psql -U cosmos -d cosmos \
--     -v ON_ERROR_STOP=1 -f load_full_reprocess.sql
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 왜 cosmos_analysis 가 아니라 public 인가
-- ─────────────────────────────────────────────────────────────────────────────
-- 백엔드는 cosmos 롤로 붙고 search_path 가 "$user", public 이다. GraphQueryRepository
-- 의 SQL 은 전부 스키마를 안 붙이므로 실제로 읽는 것은 public.* 다. 실측으로
-- to_regclass('graph_snapshot') = public.graph_snapshot 이고, 운영 API 가 돌려주는
-- snapshot ab19d794 는 public.graph_snapshot 의 유일한 행이다.
-- cosmos_analysis 의 run/active_run 구조는 서빙 경로에 연결돼 있지 않다.
-- (2026-09-20 에 cosmos_analysis 로 넣은 관계가 화면에 안 나온 이유가 이것이다.)
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 창(window)
-- ─────────────────────────────────────────────────────────────────────────────
-- 분석 창은 30D/1Y/10Y 로 정했지만 7D/90D 행도 같이 넣는다. 지금 백엔드
-- MetricWindow 는 7D/30D/90D 만 받으므로, 새 스냅샷으로 갈아끼운 뒤 그 두 탭이
-- 비어 보이지 않게 하려는 것이다. 1Y/10Y 는 백엔드·프론트에 창을 추가해야 보인다.
--
-- 되돌리기: rollback_full_reprocess.sql

\set ON_ERROR_STOP on
BEGIN;

-- 0) 되돌릴 수 있게 지금 상태를 통째로 떠 둔다 (373행).
DROP TABLE IF EXISTS public.relationship_score_current_bak_20260922;
CREATE TABLE public.relationship_score_current_bak_20260922 AS
    SELECT * FROM public.relationship_score_current;

CREATE TEMP TABLE _rel_type (code text, name text, directionality text) ON COMMIT DROP;
CREATE TEMP TABLE _snapshot (as_of_at timestamptz, formula_version text, model_version text,
                             status text, hdfs_uri text, published_at timestamptz) ON COMMIT DROP;
CREATE TEMP TABLE _rel (source_market text, source_stock_code text, target_market text,
                        target_stock_code text, relationship_type_code text) ON COMMIT DROP;
CREATE TEMP TABLE _score (source_market text, source_stock_code text, target_market text,
                          target_stock_code text, relationship_type_code text, window_type text,
                          news_score numeric, disclosure_score numeric, score numeric,
                          impact_direction text, confidence numeric, evidence_count int,
                          formula_version text, as_of_at timestamptz) ON COMMIT DROP;

\copy _rel_type FROM 'relationship_type.csv'          WITH (FORMAT csv, HEADER true)
\copy _snapshot FROM 'graph_snapshot.csv'             WITH (FORMAT csv, HEADER true)
\copy _rel      FROM 'company_relationship.csv'       WITH (FORMAT csv, HEADER true)
\copy _score    FROM 'relationship_score_current.csv' WITH (FORMAT csv, HEADER true)

-- 1) 기업 사전에 없는 종목이 있으면 조용히 버리지 말고 멈춘다.
DO $guard$
DECLARE missing int;
BEGIN
    SELECT count(*) INTO missing FROM (
        SELECT source_market m, source_stock_code c FROM _rel
        UNION SELECT target_market, target_stock_code FROM _rel) t
    WHERE NOT EXISTS (SELECT 1 FROM public.company x
                       WHERE x.market = t.m AND x.stock_code = t.c AND x.status = 'ACTIVE');
    IF missing > 0 THEN
        RAISE EXCEPTION 'company 에 없거나 ACTIVE 가 아닌 종목 %개 — 적재 중단', missing;
    END IF;
END $guard$;

-- 2) 관계 유형 4종.
INSERT INTO public.relationship_type (code, name, directionality)
SELECT code, name, directionality FROM _rel_type
ON CONFLICT (code) DO NOTHING;

-- 3) 이번 발행 스냅샷. 같은 (as_of_at, formula_version) 이면 재사용한다.
INSERT INTO public.graph_snapshot
    (as_of_at, formula_version, model_version, status, hdfs_uri, published_at)
SELECT s.as_of_at, s.formula_version, s.model_version, s.status, s.hdfs_uri, s.published_at
FROM _snapshot s
WHERE NOT EXISTS (SELECT 1 FROM public.graph_snapshot g
                   WHERE g.as_of_at = s.as_of_at AND g.formula_version = s.formula_version);

-- 4) 관계. 자연키(기업쌍, 유형)가 이미 있으면 그 행을 그대로 쓴다.
INSERT INTO public.company_relationship
    (source_company_id, target_company_id, relationship_type_id)
SELECT sc.company_id, tc.company_id, rt.relationship_type_id
FROM _rel r
JOIN public.company sc ON sc.market = r.source_market AND sc.stock_code = r.source_stock_code
JOIN public.company tc ON tc.market = r.target_market AND tc.stock_code = r.target_stock_code
JOIN public.relationship_type rt ON rt.code = r.relationship_type_code
ON CONFLICT ON CONSTRAINT uk_company_relationship_identity DO NOTHING;

-- 5) 창별 점수. PK 가 (relationship_id, window_type) 라 같은 관계의 예전 창 점수는
--    이번 스냅샷 값으로 덮인다 (0 단계 백업으로 되돌릴 수 있다).
INSERT INTO public.relationship_score_current
    (relationship_id, window_type, snapshot_id, score, news_score, disclosure_score,
     impact_direction, confidence, evidence_count, formula_version, as_of_at)
SELECT cr.relationship_id, s.window_type, g.snapshot_id, s.score,
       s.news_score, s.disclosure_score, NULLIF(s.impact_direction, ''),
       s.confidence, s.evidence_count, s.formula_version, s.as_of_at
FROM _score s
JOIN public.company sc ON sc.market = s.source_market AND sc.stock_code = s.source_stock_code
JOIN public.company tc ON tc.market = s.target_market AND tc.stock_code = s.target_stock_code
JOIN public.relationship_type rt ON rt.code = s.relationship_type_code
JOIN public.company_relationship cr
       ON cr.source_company_id = sc.company_id
      AND cr.target_company_id = tc.company_id
      AND cr.relationship_type_id = rt.relationship_type_id
JOIN public.graph_snapshot g
       ON g.as_of_at = s.as_of_at AND g.formula_version = s.formula_version
ON CONFLICT (relationship_id, window_type)
DO UPDATE SET snapshot_id = EXCLUDED.snapshot_id, score = EXCLUDED.score,
              news_score = EXCLUDED.news_score, disclosure_score = EXCLUDED.disclosure_score,
              impact_direction = EXCLUDED.impact_direction, confidence = EXCLUDED.confidence,
              evidence_count = EXCLUDED.evidence_count,
              formula_version = EXCLUDED.formula_version, as_of_at = EXCLUDED.as_of_at;

-- 6) 넣으려던 행이 전부 들어갔는지 확인한다. 하나라도 빠지면 멈춘다.
DO $verify$
DECLARE loaded int; expected int;
BEGIN
    SELECT count(*) INTO expected FROM _score;
    SELECT count(*) INTO loaded FROM public.relationship_score_current
     WHERE formula_version = 'rel-v0.5-full';
    IF loaded <> expected THEN
        RAISE EXCEPTION '점수 행 불일치: 기대 % · 실제 %', expected, loaded;
    END IF;
END $verify$;

-- 7) 커밋 전 결과. 백엔드는 as_of_at 이 가장 늦은 PUBLISHED 스냅샷 하나만 읽는다.
SELECT g.as_of_at, g.formula_version, s.window_type, count(*) AS 관계
FROM public.relationship_score_current s
JOIN public.graph_snapshot g ON g.snapshot_id = s.snapshot_id
GROUP BY 1, 2, 3
ORDER BY 1, CASE s.window_type WHEN '7D' THEN 1 WHEN '30D' THEN 2 WHEN '90D' THEN 3
                               WHEN '1Y' THEN 4 ELSE 5 END;

COMMIT;
