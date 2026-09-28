-- load_into_analysis_run.sql 을 되돌린다. 이번에 넣은 행만 지운다.
--
--   psql -v ON_ERROR_STOP=1 -v run_id=lake-grounded-20260919-v1 -f rollback_expanded_relations.sql
--
-- 기준은 formula_version = 'rel-v0.3-expanded' 다. 기존 119관계는 다른 formula_version 을
-- 쓰므로 건드리지 않는다. company_relationship 행도 이번에 새로 생긴 것만 지운다
-- (점수 행이 하나도 남지 않은 관계 = 이번에 추가된 것).

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE _target AS
SELECT COALESCE(NULLIF(:'run_id', ''), (SELECT run_id FROM cosmos_analysis.active_run))::text AS run_id;

-- 1) 이번 발행의 점수 행
DELETE FROM cosmos_analysis.relationship_score_current s
USING _target t
WHERE s.run_id = t.run_id AND s.formula_version = 'rel-v0.3-expanded';

-- 2) 점수 행이 하나도 안 남은 관계 = 이번에 추가된 관계
DELETE FROM cosmos_analysis.company_relationship cr
USING _target t
WHERE cr.run_id = t.run_id
  AND NOT EXISTS (SELECT 1 FROM cosmos_analysis.relationship_score_current s
                   WHERE s.run_id = cr.run_id AND s.relationship_id = cr.relationship_id);

-- 3) 이번 발행의 스냅샷
DELETE FROM cosmos_analysis.graph_snapshot g
USING _target t
WHERE g.run_id = t.run_id AND g.formula_version = 'rel-v0.3-expanded'
  AND NOT EXISTS (SELECT 1 FROM cosmos_analysis.relationship_score_current s
                   WHERE s.run_id = g.run_id AND s.snapshot_id = g.snapshot_id);

SELECT formula_version, count(*) AS 남은행
FROM cosmos_analysis.relationship_score_current s
JOIN _target t ON t.run_id = s.run_id
GROUP BY formula_version ORDER BY formula_version;

COMMIT;
