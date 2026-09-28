-- 관계 시드 적재. 자연키를 UUID로 해석한다. 여러 번 돌려도 안전하다(ON CONFLICT).
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
