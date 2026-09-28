-- 영문 뉴스에서 뽑은 관계 54개를 운영 DB 에 넣는다.
--
--   psql -v ON_ERROR_STOP=1 -f load_en_relations.sql
--
-- 기존 확장 관계(rel-v0.3-expanded)가 쓰는 **같은 스냅샷**에 더한다. 새 스냅샷을 만들면
-- 백엔드가 as_of_at 최신 하나만 보므로 기존 193개가 통째로 사라진다.
--
-- 근거 문서는 이미 운영 DB 에 있다 (영문 기사도 같은 뉴스 적재 경로를 탔다).
-- 공시 때와 달리 문서를 새로 만들 필요가 없다.
--
-- 멱등하다. 여러 번 돌려도 같은 결과다.

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE _rel (source_market text, source_stock_code text, target_market text,
                        target_stock_code text, relationship_type_code text) ON COMMIT DROP;
CREATE TEMP TABLE _score (source_market text, source_stock_code text, target_market text,
                          target_stock_code text, relationship_type_code text, window_type text,
                          score numeric, news_score numeric, disclosure_score numeric,
                          impact_direction text, confidence numeric, evidence_count int,
                          formula_version text, as_of_at timestamptz) ON COMMIT DROP;
CREATE TEMP TABLE _ev (source_market text, source_stock_code text, target_market text,
                       target_stock_code text, relationship_type_code text,
                       document_id uuid) ON COMMIT DROP;

\copy _rel   FROM 'company_relationship.csv'       WITH (FORMAT csv, HEADER true)
\copy _score FROM 'relationship_score_current.csv' WITH (FORMAT csv, HEADER true)
\copy _ev    FROM 'relationship_evidence.csv'      WITH (FORMAT csv, HEADER true)

CREATE TEMP TABLE _t AS SELECT run_id FROM cosmos_analysis.active_run;
-- 기존 확장 관계가 쓰는 스냅샷을 그대로 쓴다
CREATE TEMP TABLE _snap AS
SELECT snapshot_id FROM cosmos_analysis.current_graph_snapshot
WHERE status = 'PUBLISHED' ORDER BY as_of_at DESC, snapshot_id DESC LIMIT 1;

-- 1) 관계. 같은 run 안에 (기업쌍, 종류) 가 있으면 그대로 쓴다.
INSERT INTO cosmos_analysis.company_relationship
    (run_id, relationship_id, source_company_id, target_company_id, relationship_type_id)
SELECT t.run_id, gen_random_uuid(), sc.company_id, tc.company_id, rt.relationship_type_id
FROM _rel r CROSS JOIN _t t
JOIN company sc ON sc.market = r.source_market AND sc.stock_code = r.source_stock_code
JOIN company tc ON tc.market = r.target_market AND tc.stock_code = r.target_stock_code
JOIN relationship_type rt ON rt.code = r.relationship_type_code
WHERE NOT EXISTS (
    SELECT 1 FROM cosmos_analysis.company_relationship x
     WHERE x.run_id = t.run_id AND x.source_company_id = sc.company_id
       AND x.target_company_id = tc.company_id
       AND x.relationship_type_id = rt.relationship_type_id);

-- 2) 기간 점수. 뉴스 근거만 있으므로 disclosure_score 는 NULL 이다.
INSERT INTO cosmos_analysis.relationship_score_current
    (run_id, relationship_id, window_type, snapshot_id, score, news_score, disclosure_score,
     impact_direction, confidence, evidence_count, formula_version, as_of_at)
SELECT t.run_id, cr.relationship_id, s.window_type, sn.snapshot_id, s.score,
       s.news_score, NULLIF(s.disclosure_score, NULL), s.impact_direction,
       s.confidence, s.evidence_count, s.formula_version, s.as_of_at
FROM _score s CROSS JOIN _t t CROSS JOIN _snap sn
JOIN company sc ON sc.market = s.source_market AND sc.stock_code = s.source_stock_code
JOIN company tc ON tc.market = s.target_market AND tc.stock_code = s.target_stock_code
JOIN relationship_type rt ON rt.code = s.relationship_type_code
JOIN cosmos_analysis.company_relationship cr
       ON cr.run_id = t.run_id AND cr.source_company_id = sc.company_id
      AND cr.target_company_id = tc.company_id
      AND cr.relationship_type_id = rt.relationship_type_id
ON CONFLICT (run_id, relationship_id, window_type)
DO UPDATE SET snapshot_id = EXCLUDED.snapshot_id, score = EXCLUDED.score,
              news_score = EXCLUDED.news_score,
              impact_direction = EXCLUDED.impact_direction,
              confidence = EXCLUDED.confidence, evidence_count = EXCLUDED.evidence_count,
              formula_version = EXCLUDED.formula_version, as_of_at = EXCLUDED.as_of_at;

-- 3) 근거 연결. 문서가 이 run 에 없으면 그 근거는 건너뛴다 (없는 문서를 가리키지 않는다).
INSERT INTO cosmos_analysis.relationship_evidence
-- ⚠️ evidence_id 를 넣지 않는다 — 알려진 결함이다 (2026-09-21 백엔드 지적).
-- 화면은 relationship_evidence.evidence_id 로 document_evidence 의 **근거 문장**을 찾는다.
-- 이 로더는 document_id 까지만 연결하므로 문장이 안 뜬다 (이 적재분 1,031행이 해당).
--
-- 고치는 방법: 관계를 만든 그 문장을 document_evidence 에서 찾아 evidence_id 를 함께 넣는다.
--   국내  artifacts/.../temporal_evidence.jsonl 의 quote
--   영문  data/en_rel_hits.jsonl 의 sentence
-- 원문 문장을 이미 갖고 있으므로 재추출이 아니라 매칭이면 된다.
-- 같은 문서의 아무 문장이나 붙이면 관계와 무관한 문장이 노출되므로 그렇게 하면 안 된다.
    (run_id, relationship_evidence_id, relationship_id, document_id, formula_version)
SELECT t.run_id, gen_random_uuid(), cr.relationship_id, e.document_id, 'rel-v0.4-en-news'
FROM _ev e CROSS JOIN _t t
JOIN company sc ON sc.market = e.source_market AND sc.stock_code = e.source_stock_code
JOIN company tc ON tc.market = e.target_market AND tc.stock_code = e.target_stock_code
JOIN relationship_type rt ON rt.code = e.relationship_type_code
JOIN cosmos_analysis.company_relationship cr
       ON cr.run_id = t.run_id AND cr.source_company_id = sc.company_id
      AND cr.target_company_id = tc.company_id
      AND cr.relationship_type_id = rt.relationship_type_id
JOIN cosmos_analysis.source_document d
       ON d.run_id = t.run_id AND d.document_id = e.document_id
WHERE NOT EXISTS (
    SELECT 1 FROM cosmos_analysis.relationship_evidence re
     WHERE re.run_id = t.run_id AND re.relationship_id = cr.relationship_id
       AND re.document_id = e.document_id);

-- 4) 화면의 근거 개수를 실제 연결 수로 맞춘다.
UPDATE cosmos_analysis.relationship_score_current s
   SET evidence_count = cnt.n
  FROM _t t,
       (SELECT relationship_id, count(DISTINCT document_id) n
          FROM cosmos_analysis.relationship_evidence
         WHERE run_id = (SELECT run_id FROM _t) GROUP BY 1) cnt
 WHERE s.run_id = t.run_id AND s.relationship_id = cnt.relationship_id
   AND s.formula_version = 'rel-v0.4-en-news';

SELECT formula_version, count(*) AS 관계, sum(evidence_count) AS 근거,
       min(round(score,1)) AS 최저, max(round(score,1)) AS 최고
FROM cosmos_analysis.relationship_score_current
WHERE run_id = (SELECT run_id FROM _t) AND window_type = '30D'
GROUP BY 1 ORDER BY 1;

COMMIT;
