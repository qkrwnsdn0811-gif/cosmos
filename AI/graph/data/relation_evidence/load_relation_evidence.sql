-- 확장 관계 193개의 근거를 붙인다. load_into_analysis_run.sql 이 빠뜨린 부분이다.
--
--   psql -v ON_ERROR_STOP=1 -f load_relation_evidence.sql
--
-- 화면은 근거 개수를 relationship_score_current.evidence_count 에서 읽고
-- 근거 목록은 relationship_evidence -> source_document 를 타고 읽는다. 앞의 것만 넣어서
-- "근거 문서 16건" 이라고 떠 놓고 목록은 비는 상태가 됐다.
--
-- 세 단계다
--   1) 운영 DB 에 없는 DART 공시 문서 894건을 만든다 (접수번호·제목·URL·접수일만. 본문 없음)
--   2) 근거 1,614건을 relationship_evidence 에 넣는다
--   3) evidence_count 를 실제로 연결된 근거 수로 맞춘다 — 화면 숫자와 목록이 어긋나지 않게
--
-- 멱등하다. 여러 번 돌려도 같은 결과다.

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE _doc (document_id uuid, document_type text, title text,
                        original_url text, published_at text, rcept_no text) ON COMMIT DROP;
CREATE TEMP TABLE _ev (rel_type text, src text, dst text, source_kind text,
                       uuid text, rcept_no text, source_url text) ON COMMIT DROP;

\copy _doc FROM 'new_disclosure_documents.csv' WITH (FORMAT csv, HEADER true)
\copy _ev  FROM 'evidence_link.csv'            WITH (FORMAT csv, HEADER true)

CREATE TEMP TABLE _target AS
SELECT run_id FROM cosmos_analysis.active_run;

-- 1) 없는 공시 문서를 만든다. 본문(hdfs_*)은 채우지 않는다 — 갖고 있지 않다.
INSERT INTO cosmos_analysis.source_document
    (run_id, document_id, document_type, title, original_url, published_at, status)
SELECT t.run_id, d.document_id, d.document_type, NULLIF(d.title, ''),
       NULLIF(d.original_url, ''), NULLIF(d.published_at, '')::timestamptz, 'PUBLISHED'
FROM _doc d CROSS JOIN _target t
WHERE NOT EXISTS (SELECT 1 FROM cosmos_analysis.source_document x
                   WHERE x.run_id = t.run_id AND x.document_id = d.document_id)
  AND NOT EXISTS (SELECT 1 FROM cosmos_analysis.source_document y
                   WHERE y.run_id = t.run_id AND y.original_url = d.original_url);

-- 2) 근거를 관계에 붙인다. 문서는 uuid -> dart URL -> 뉴스 URL 순으로 찾는다.
CREATE TEMP TABLE _resolved ON COMMIT DROP AS
SELECT DISTINCT r.relationship_id, COALESCE(a.document_id, b.document_id, c.document_id) AS document_id
FROM _ev e
CROSS JOIN _target t
JOIN company sc ON sc.stock_code = e.src
JOIN company tc ON tc.stock_code = e.dst
JOIN relationship_type rt ON rt.code = e.rel_type
JOIN cosmos_analysis.company_relationship r
       ON r.run_id = t.run_id AND r.source_company_id = sc.company_id
      AND r.target_company_id = tc.company_id
      AND r.relationship_type_id = rt.relationship_type_id
LEFT JOIN cosmos_analysis.source_document a
       ON a.run_id = t.run_id AND e.uuid <> '' AND a.document_id = e.uuid::uuid
LEFT JOIN cosmos_analysis.source_document b
       ON b.run_id = t.run_id AND e.rcept_no <> ''
      AND b.original_url = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' || e.rcept_no
LEFT JOIN cosmos_analysis.source_document c
       ON c.run_id = t.run_id AND e.source_url <> '' AND c.original_url = e.source_url
WHERE COALESCE(a.document_id, b.document_id, c.document_id) IS NOT NULL;

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
SELECT t.run_id, gen_random_uuid(), x.relationship_id, x.document_id, 'rel-v0.3-expanded'
FROM _resolved x CROSS JOIN _target t
WHERE NOT EXISTS (SELECT 1 FROM cosmos_analysis.relationship_evidence re
                   WHERE re.run_id = t.run_id AND re.relationship_id = x.relationship_id
                     AND re.document_id = x.document_id);

-- 3) 화면의 근거 개수를 실제 연결 수로 맞춘다. 어긋나면 "16건" 이라 써 놓고 목록이 빈다.
UPDATE cosmos_analysis.relationship_score_current s
   SET evidence_count = cnt.n
  FROM _target t,
       (SELECT relationship_id, count(DISTINCT document_id) n
          FROM cosmos_analysis.relationship_evidence
         WHERE run_id = (SELECT run_id FROM _target)
         GROUP BY 1) cnt
 WHERE s.run_id = t.run_id AND s.relationship_id = cnt.relationship_id
   AND s.formula_version = 'rel-v0.3-expanded';

-- 근거가 하나도 안 붙은 관계는 0 으로. 없는 숫자를 남겨두지 않는다.
UPDATE cosmos_analysis.relationship_score_current s
   SET evidence_count = 0
  FROM _target t
 WHERE s.run_id = t.run_id AND s.formula_version = 'rel-v0.3-expanded'
   AND NOT EXISTS (SELECT 1 FROM cosmos_analysis.relationship_evidence re
                    WHERE re.run_id = t.run_id AND re.relationship_id = s.relationship_id);

SELECT count(*) AS 근거행, count(DISTINCT relationship_id) AS 근거붙은관계
FROM cosmos_analysis.relationship_evidence WHERE run_id = (SELECT run_id FROM _target);

SELECT count(*) AS 관계, count(*) FILTER (WHERE evidence_count > 0) AS 근거있음,
       count(*) FILTER (WHERE evidence_count = 0) AS 근거없음
FROM cosmos_analysis.relationship_score_current
WHERE run_id = (SELECT run_id FROM _target) AND window_type = '30D';

COMMIT;
