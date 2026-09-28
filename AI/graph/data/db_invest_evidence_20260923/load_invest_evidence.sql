-- INVEST 관계의 공시 근거를 운영 public 스키마에 적재한다.
--
--   docker exec -w /tmp/inv -i cosmos-postgres-1 psql -U cosmos -d cosmos \
--     -v ON_ERROR_STOP=1 -f load_invest_evidence.sql
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 왜 필요한가
-- ─────────────────────────────────────────────────────────────────────────────
-- 관계 4종 중 INVEST 만 뉴스가 아니라 DART 지분 공시에서 나온다. 인용할 기사 문장이
-- 없어서 화면에 "근거 문서 1건" 이라 떠 놓고 근거 영역이 비어 있었다.
-- 여기 넣는 문장은 공시 사실(지분율·관계구분·보고서)을 요약해 **만든 것**이다.
-- model_version 으로 그 사실이 DB 에 남고, 백엔드가 document_type 과 함께 내려보내
-- 화면에서 기사 인용문과 구분해 그린다.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 문서는 만들지 않는다
-- ─────────────────────────────────────────────────────────────────────────────
-- 지분 공시 33건은 DART 수집기가 이미 넣어 뒀다(실측 33/33 존재). 그래서
-- source_document / disclosure 는 건드리지 않고 dart_receipt_no 로 찾아 붙이기만 한다.
--
-- company_document 는 document_evidence 의 FK 부모라 없으면 만든다. 공시 문서에는
-- 제출사(FILER) 행만 있고 지분을 **보유한 쪽** 행이 없기 때문이다.
-- 그 행은 mention_type='MENTION' 이고 is_service_visible=false 다 — 뉴스 목록에
-- 공시가 끼어들면 안 된다.
--
-- 되돌리기: rollback_invest_evidence.sql

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE _de (evidence_id uuid, dart_receipt_no text, market text, stock_code text,
                       sentence_text text, sentence_order int, confidence numeric,
                       model_version text) ON COMMIT DROP;
CREATE TEMP TABLE _re (source_market text, source_stock_code text, target_market text,
                       target_stock_code text, relationship_type_code text,
                       dart_receipt_no text, evidence_id uuid, contribution_score numeric,
                       model_version text, formula_version text) ON COMMIT DROP;

\copy _de FROM 'document_evidence.csv'     WITH (FORMAT csv, HEADER true)
\copy _re FROM 'relationship_evidence.csv' WITH (FORMAT csv, HEADER true)

-- 0) 공시가 전부 DB 에 있는지. 하나라도 없으면 멈춘다.
DO $guard$
DECLARE missing int;
BEGIN
    SELECT count(DISTINCT d.dart_receipt_no) INTO missing
    FROM _de d
    WHERE NOT EXISTS (SELECT 1 FROM public.disclosure x
                       WHERE x.dart_receipt_no = d.dart_receipt_no);
    IF missing > 0 THEN
        RAISE EXCEPTION 'DB 에 없는 지분 공시 %건 — 적재 중단', missing;
    END IF;
END $guard$;

-- 1) 문서-기업. 지분을 보유한 쪽 행을 만든다. 이미 있으면 건드리지 않는다.
INSERT INTO public.company_document
    (document_id, company_id, mention_type, model_version, is_service_visible, analyzed_at)
SELECT dc.document_id, co.company_id, 'MENTION', e.model_version, false,
       COALESCE(dc.filing_date::TIMESTAMPTZ, CURRENT_TIMESTAMP)
FROM _de e
JOIN public.disclosure dc ON dc.dart_receipt_no = e.dart_receipt_no
JOIN public.company co ON co.market = e.market AND co.stock_code = e.stock_code
ON CONFLICT (document_id, company_id) DO NOTHING;

-- 2) 근거 문장.
INSERT INTO public.document_evidence
    (evidence_id, document_id, company_id, sentence_text, sentence_order, confidence, model_version)
SELECT e.evidence_id, dc.document_id, co.company_id, e.sentence_text, e.sentence_order,
       e.confidence, e.model_version
FROM _de e
JOIN public.disclosure dc ON dc.dart_receipt_no = e.dart_receipt_no
JOIN public.company co ON co.market = e.market AND co.stock_code = e.stock_code
JOIN public.company_document cd
       ON cd.document_id = dc.document_id AND cd.company_id = co.company_id
ON CONFLICT (evidence_id) DO NOTHING;

-- 3) 관계-근거 연결.
INSERT INTO public.relationship_evidence
    (relationship_id, document_id, evidence_id, contribution_score, model_version, formula_version)
SELECT cr.relationship_id, dc.document_id, r.evidence_id, r.contribution_score,
       r.model_version, r.formula_version
FROM _re r
JOIN public.disclosure dc ON dc.dart_receipt_no = r.dart_receipt_no
JOIN public.company sc ON sc.market = r.source_market AND sc.stock_code = r.source_stock_code
JOIN public.company tc ON tc.market = r.target_market AND tc.stock_code = r.target_stock_code
JOIN public.relationship_type rt ON rt.code = r.relationship_type_code
JOIN public.company_relationship cr
       ON cr.source_company_id = sc.company_id
      AND cr.target_company_id = tc.company_id
      AND cr.relationship_type_id = rt.relationship_type_id
JOIN public.document_evidence de
       ON de.evidence_id = r.evidence_id AND de.document_id = dc.document_id
ON CONFLICT ON CONSTRAINT uk_relationship_evidence_identity DO NOTHING;

-- 4) 커밋 전 확인.
SELECT '근거 문장' AS 항목, (SELECT count(*) FROM _de) AS 입력,
       (SELECT count(*) FROM public.document_evidence de JOIN _de e USING (evidence_id)) AS DB
UNION ALL SELECT '관계-근거', (SELECT count(*) FROM _re),
       (SELECT count(*) FROM public.relationship_evidence re JOIN _re r USING (evidence_id));

-- 백엔드 근거 조회가 실제로 통과하는 사슬인지 (수정된 쿼리 기준).
SELECT count(*) AS 조회가능, count(DISTINCT re.relationship_id) AS 관계
FROM public.relationship_evidence re
JOIN public.source_document sd ON sd.document_id = re.document_id
LEFT JOIN public.news_article na ON na.document_id = sd.document_id
LEFT JOIN public.disclosure dc ON dc.document_id = sd.document_id
LEFT JOIN public.document_evidence de ON de.evidence_id = re.evidence_id
WHERE sd.document_type = 'DISCLOSURE'
  AND COALESCE(sd.published_at, dc.filing_date::TIMESTAMPTZ) IS NOT NULL;

COMMIT;
