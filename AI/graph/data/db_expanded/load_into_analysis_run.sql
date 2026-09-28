-- 확장 관계 193개를 운영 cosmos_analysis 스키마에 적재한다.
--
--   psql -v ON_ERROR_STOP=1 -v run_id=lake-grounded-20260919-v1 -f load_into_analysis_run.sql
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 왜 새 run 을 만들지 않고 기존 run 에 넣는가
-- ─────────────────────────────────────────────────────────────────────────────
-- cosmos_analysis 의 current_* 뷰는 active_run 이 가리키는 run 하나만 읽는다.
-- 관계만 든 새 run 을 만들어 active_run 을 돌리면 그 run 에는 뉴스·기업연결·감성이
-- 0건이므로 화면 전체가 빈다. 뉴스 219,662건과 연결 246,117건이 사라진다.
-- 12개 버전 테이블을 통째로 복제한 뒤 전환하는 방법도 있으나 100만 행 가까이 복사해야 한다.
-- 그래서 기존 활성 run 에 관계·점수 행만 더한다. active_run 은 건드리지 않는다.
--
-- 되돌리기는 rollback_expanded_relations.sql 이다. formula_version 으로 이번에 넣은
-- 행만 정확히 골라낼 수 있다 ('rel-v0.3-expanded').
-- ─────────────────────────────────────────────────────────────────────────────
-- 기존 119관계는 지우지 않는다. 같은 (기업쌍, 관계종류) 가 겹치면 점수만 갱신된다.
-- company / relationship_type 은 버전 테이블이 아니므로 public 을 그대로 참조한다.

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE _rel_type (code text, name text, directionality text) ON COMMIT DROP;
CREATE TEMP TABLE _snapshot (as_of_at timestamptz, formula_version text, model_version text,
                             status text, hdfs_uri text, published_at timestamptz) ON COMMIT DROP;
CREATE TEMP TABLE _rel (source_market text, source_stock_code text, target_market text,
                        target_stock_code text, relationship_type_code text) ON COMMIT DROP;
CREATE TEMP TABLE _score (source_market text, source_stock_code text, target_market text,
                          target_stock_code text, relationship_type_code text, window_type text,
                          score numeric, news_score numeric, disclosure_score numeric,
                          impact_direction text, confidence numeric, evidence_count int,
                          formula_version text, as_of_at timestamptz) ON COMMIT DROP;

\copy _rel_type FROM 'relationship_type.csv'          WITH (FORMAT csv, HEADER true)
\copy _snapshot FROM 'graph_snapshot.csv'             WITH (FORMAT csv, HEADER true)
\copy _rel      FROM 'company_relationship.csv'       WITH (FORMAT csv, HEADER true)
\copy _score    FROM 'relationship_score_current.csv' WITH (FORMAT csv, HEADER true)

-- 대상 run 은 인자로 받는다. 지정하지 않으면 현재 활성 run 에 넣는다.
CREATE TEMP TABLE _target AS
SELECT COALESCE(NULLIF(:'run_id', ''), (SELECT run_id FROM cosmos_analysis.active_run))::text AS run_id;

DO $$
DECLARE target text;
BEGIN
    SELECT run_id INTO target FROM _target;
    IF target IS NULL THEN
        RAISE EXCEPTION '대상 run 을 정할 수 없다. active_run 이 비어 있고 -v run_id 도 없다';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM cosmos_analysis.runs WHERE run_id = target) THEN
        RAISE EXCEPTION '존재하지 않는 run: %', target;
    END IF;
    RAISE NOTICE '대상 run = %', target;
END $$;

-- 관계 유형은 public 공용이다. 없으면 만든다.
INSERT INTO relationship_type (code, name, directionality)
SELECT code, name, directionality FROM _rel_type
ON CONFLICT (code) DO NOTHING;

-- 1) 이번 발행용 스냅샷. 같은 (run, as_of_at, formula_version) 이면 재사용한다.
INSERT INTO cosmos_analysis.graph_snapshot
    (run_id, snapshot_id, as_of_at, formula_version, model_version, status, hdfs_uri, published_at)
SELECT t.run_id, gen_random_uuid(), s.as_of_at, s.formula_version, s.model_version,
       s.status, s.hdfs_uri, s.published_at
FROM _snapshot s CROSS JOIN _target t
WHERE NOT EXISTS (
    SELECT 1 FROM cosmos_analysis.graph_snapshot g
     WHERE g.run_id = t.run_id AND g.as_of_at = s.as_of_at
       AND g.formula_version = s.formula_version);

-- 2) 관계. 같은 run 안에서 (기업쌍, 종류) 가 이미 있으면 그 행을 그대로 쓴다.
INSERT INTO cosmos_analysis.company_relationship
    (run_id, relationship_id, source_company_id, target_company_id, relationship_type_id)
SELECT t.run_id, gen_random_uuid(), sc.company_id, tc.company_id, rt.relationship_type_id
FROM _rel r
CROSS JOIN _target t
JOIN company sc ON sc.market = r.source_market AND sc.stock_code = r.source_stock_code
JOIN company tc ON tc.market = r.target_market AND tc.stock_code = r.target_stock_code
JOIN relationship_type rt ON rt.code = r.relationship_type_code
WHERE NOT EXISTS (
    SELECT 1 FROM cosmos_analysis.company_relationship x
     WHERE x.run_id = t.run_id AND x.source_company_id = sc.company_id
       AND x.target_company_id = tc.company_id
       AND x.relationship_type_id = rt.relationship_type_id);

-- 3) 기간 점수. news_score / disclosure_score 는 근거가 없으면 NULL 로 둔다.
--    프론트(score.ts blendScore)가 NULL 쪽을 빼고 있는 쪽 점수를 그대로 쓴다.
INSERT INTO cosmos_analysis.relationship_score_current
    (run_id, relationship_id, window_type, snapshot_id, score, news_score, disclosure_score,
     impact_direction, confidence, evidence_count, formula_version, as_of_at)
SELECT t.run_id, cr.relationship_id, s.window_type, g.snapshot_id, s.score,
       s.news_score, s.disclosure_score, NULLIF(s.impact_direction, ''),
       s.confidence, s.evidence_count, s.formula_version, s.as_of_at
FROM _score s
CROSS JOIN _target t
JOIN company sc ON sc.market = s.source_market AND sc.stock_code = s.source_stock_code
JOIN company tc ON tc.market = s.target_market AND tc.stock_code = s.target_stock_code
JOIN relationship_type rt ON rt.code = s.relationship_type_code
JOIN cosmos_analysis.company_relationship cr
       ON cr.run_id = t.run_id AND cr.source_company_id = sc.company_id
      AND cr.target_company_id = tc.company_id
      AND cr.relationship_type_id = rt.relationship_type_id
JOIN cosmos_analysis.graph_snapshot g
       ON g.run_id = t.run_id AND g.as_of_at = s.as_of_at
      AND g.formula_version = s.formula_version
ON CONFLICT (run_id, relationship_id, window_type)
DO UPDATE SET snapshot_id = EXCLUDED.snapshot_id, score = EXCLUDED.score,
              news_score = EXCLUDED.news_score, disclosure_score = EXCLUDED.disclosure_score,
              impact_direction = EXCLUDED.impact_direction, confidence = EXCLUDED.confidence,
              evidence_count = EXCLUDED.evidence_count,
              formula_version = EXCLUDED.formula_version, as_of_at = EXCLUDED.as_of_at;

-- 적재 결과를 커밋 전에 확인한다. 기대치와 다르면 ROLLBACK 한다.
SELECT s.formula_version,
       count(*)                                                      AS 점수행,
       count(s.news_score)                                           AS 뉴스점수,
       count(s.disclosure_score)                                     AS 공시점수,
       count(*) FILTER (WHERE s.news_score IS NOT NULL
                          AND s.disclosure_score IS NOT NULL)        AS 슬라이더동작
FROM cosmos_analysis.relationship_score_current s
JOIN _target t ON t.run_id = s.run_id
GROUP BY s.formula_version
ORDER BY s.formula_version;

COMMIT;
