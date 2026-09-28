-- 관계 근거 문장을 운영 public 스키마에 적재한다.
--
--   docker exec -w /tmp/evi -i cosmos-postgres-1 psql -U cosmos -d cosmos \
--     -v ON_ERROR_STOP=1 -f load_evidence.sql
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 왜 이 다섯 테이블을 다 넣어야 하는가
-- ─────────────────────────────────────────────────────────────────────────────
-- 백엔드 GraphQueryRepository 의 근거 조회는 이 사슬을 전부 탄다:
--
--   relationship_evidence -> source_document -> news_article
--                         -> document_evidence(문장)   [LEFT JOIN]
--   document_evidence -> company_document (FK)
--
-- 그래서 relationship_evidence 만 넣으면 아무것도 안 보인다. 적재 전 운영 DB 는
-- relationship_evidence·document_evidence 가 **둘 다 0행**이었다.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- document_id 는 우리가 정하지 않고 DB 에서 찾는다
-- ─────────────────────────────────────────────────────────────────────────────
-- 수집기 규칙은 uuid5(NAMESPACE_URL, 'cosmos:document:NEWS:' + sha256(canonical_url))
-- 인데, 실측으로 근거 문서 8,926건 중 **3,591건이 이미 news_article 에 같은
-- canonical_url_hash 로 있으면서 document_id 가 다르다**(예전 적재가 다른 규칙을 썼다).
-- 그 3,591건에 새 id 로 news_article 을 넣으면 UNIQUE 제약에 걸려 조용히 버려지고,
-- 근거 조회의 JOIN news_article 에서 빠져 화면에 안 나온다.
--
-- 그래서 _docmap 을 먼저 만든다: 기존 행이 있으면 그 document_id, 없으면 uuid5 값.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 수집기와 충돌하지 않게 하는 두 가지
-- ─────────────────────────────────────────────────────────────────────────────
-- 1) content_hash 를 비우고 status 를 COLLECTED, analysis_version 을 NULL 로 둔다.
--    채우면 나중에 수집기가 같은 URL 을 다른 렌더링으로 가져올 때 배치가 통째로 실패한다
--    (document_loader/postgres.py 의 protected_news_revision 분기).
-- 2) company_document 는 DO NOTHING 이다. 수집기가 이미 분석해 둔 행을 덮지 않는다.
--
-- is_service_visible 은 false 다 — 2020~2026년 아카이브 기사가 뉴스 목록에 갑자기
-- 끼어들지 않게. 근거 패널은 이 값을 보지 않으므로 문장은 정상적으로 뜬다.
--
-- 되돌리기: rollback_evidence.sql

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE _src (source_id uuid, name text, source_type text) ON COMMIT DROP;
CREATE TEMP TABLE _doc (url_hash text, fallback_document_id uuid, source_id uuid, title text,
                        original_url text, published_at timestamptz, hdfs_raw_uri text,
                        status text) ON COMMIT DROP;
CREATE TEMP TABLE _art (url_hash text, publisher text, canonical_url text, author text) ON COMMIT DROP;
CREATE TEMP TABLE _cd (url_hash text, market text, stock_code text, mention_type text,
                       relevance_score numeric, sentiment text, impact_score numeric,
                       confidence numeric, model_version text, is_service_visible boolean,
                       analyzed_at timestamptz) ON COMMIT DROP;
CREATE TEMP TABLE _de (evidence_id uuid, url_hash text, market text, stock_code text,
                       sentence_text text, sentence_order int, confidence numeric,
                       model_version text) ON COMMIT DROP;
CREATE TEMP TABLE _re (source_market text, source_stock_code text, target_market text,
                       target_stock_code text, relationship_type_code text, url_hash text,
                       evidence_id uuid, contribution_score numeric, model_version text,
                       formula_version text) ON COMMIT DROP;

\copy _src FROM 'data_source.csv'           WITH (FORMAT csv, HEADER true)
\copy _doc FROM 'source_document.csv'       WITH (FORMAT csv, HEADER true)
\copy _art FROM 'news_article.csv'          WITH (FORMAT csv, HEADER true)
\copy _cd  FROM 'company_document.csv'      WITH (FORMAT csv, HEADER true)
\copy _de  FROM 'document_evidence.csv'     WITH (FORMAT csv, HEADER true)
\copy _re  FROM 'relationship_evidence.csv' WITH (FORMAT csv, HEADER true)

-- 0) 기업 사전에 없는 종목이 있으면 멈춘다.
DO $guard$
DECLARE missing int;
BEGIN
    SELECT count(*) INTO missing FROM (
        SELECT market m, stock_code c FROM _cd
        UNION SELECT market, stock_code FROM _de) t
    WHERE NOT EXISTS (SELECT 1 FROM public.company x
                       WHERE x.market = t.m AND x.stock_code = t.c AND x.status = 'ACTIVE');
    IF missing > 0 THEN
        RAISE EXCEPTION 'company 에 없거나 ACTIVE 가 아닌 종목 %개 — 적재 중단', missing;
    END IF;
END $guard$;

-- 1) URL 해시 -> document_id. 이미 있는 기사는 그 id 를 그대로 쓴다.
CREATE TEMP TABLE _docmap ON COMMIT DROP AS
SELECT d.url_hash,
       COALESCE(n.document_id, d.fallback_document_id) AS document_id,
       (n.document_id IS NOT NULL) AS already_loaded
FROM _doc d
LEFT JOIN public.news_article n ON n.canonical_url_hash = d.url_hash;
CREATE UNIQUE INDEX ON _docmap (url_hash);

SELECT count(*) FILTER (WHERE already_loaded) AS 이미있는_기사,
       count(*) FILTER (WHERE NOT already_loaded) AS 새로넣는_기사
FROM _docmap;

-- 2) 아카이브 출처.
INSERT INTO public.data_source (source_id, name, source_type)
SELECT source_id, name, source_type FROM _src
ON CONFLICT DO NOTHING;

-- 3) 기사 본체. 이미 있는 것은 건드리지 않는다.
INSERT INTO public.source_document
    (document_id, source_id, document_type, title, original_url, published_at,
     hdfs_raw_uri, status)
SELECT m.document_id, d.source_id, 'NEWS', d.title, d.original_url, d.published_at,
       d.hdfs_raw_uri, d.status
FROM _doc d JOIN _docmap m USING (url_hash)
WHERE NOT m.already_loaded
ON CONFLICT DO NOTHING;

INSERT INTO public.news_article
    (document_id, publisher, canonical_url, canonical_url_hash, author)
SELECT m.document_id, a.publisher, a.canonical_url, a.url_hash, a.author
FROM _art a JOIN _docmap m USING (url_hash)
WHERE NOT m.already_loaded
  AND EXISTS (SELECT 1 FROM public.source_document s WHERE s.document_id = m.document_id)
ON CONFLICT DO NOTHING;

-- 3-1) 이미 있던 기사의 빈 발행일을 채운다.
--
-- 백엔드 근거 조회는 `sd.published_at IS NOT NULL` 을 건다. 그런데 운영 NEWS 438,944건
-- 중 196,944건(45%)이 발행일이 비어 있다. 우리 근거 문서 3,596건도 여기 걸려서,
-- 안 채우면 근거 9,188건 중 5,951건만 조회된다(관계 1,211개 중 999개).
--
-- 덮어쓰는 것이 아니라 **NULL 인 것만** 채운다. 수집기의 COMMON_UPDATE 도
-- `published_at = COALESCE(새값, published_at)` 로 같은 규칙을 쓴다.
-- 되돌릴 수 있게 채운 문서를 따로 적어 둔다.
DROP TABLE IF EXISTS public.source_document_published_fill_20260922;
CREATE TABLE public.source_document_published_fill_20260922 (document_id uuid PRIMARY KEY);

WITH filled AS (
    UPDATE public.source_document s
       SET published_at = d.published_at, updated_at = CURRENT_TIMESTAMP
      FROM _doc d JOIN _docmap m USING (url_hash)
     WHERE s.document_id = m.document_id
       AND s.published_at IS NULL AND d.published_at IS NOT NULL
    RETURNING s.document_id
)
INSERT INTO public.source_document_published_fill_20260922 (document_id)
SELECT document_id FROM filled;

SELECT count(*) AS 발행일_채운_기사 FROM public.source_document_published_fill_20260922;

-- 4) 문서-기업. 수집기가 이미 분석한 행은 덮지 않는다.
INSERT INTO public.company_document
    (document_id, company_id, mention_type, relevance_score, sentiment, impact_score,
     confidence, model_version, is_service_visible, analyzed_at)
SELECT m.document_id, co.company_id, c.mention_type, c.relevance_score, c.sentiment,
       c.impact_score, c.confidence, c.model_version, c.is_service_visible,
       COALESCE(c.analyzed_at, CURRENT_TIMESTAMP)
FROM _cd c
JOIN _docmap m USING (url_hash)
JOIN public.company co ON co.market = c.market AND co.stock_code = c.stock_code
WHERE EXISTS (SELECT 1 FROM public.source_document s WHERE s.document_id = m.document_id)
ON CONFLICT (document_id, company_id) DO NOTHING;

-- 5) 근거 문장.
INSERT INTO public.document_evidence
    (evidence_id, document_id, company_id, sentence_text, sentence_order, confidence, model_version)
SELECT e.evidence_id, m.document_id, co.company_id, e.sentence_text, e.sentence_order,
       e.confidence, e.model_version
FROM _de e
JOIN _docmap m USING (url_hash)
JOIN public.company co ON co.market = e.market AND co.stock_code = e.stock_code
JOIN public.company_document cd ON cd.document_id = m.document_id AND cd.company_id = co.company_id
ON CONFLICT (evidence_id) DO NOTHING;

-- 6) 관계-근거 연결. 이번 발행에 없는 관계는 조인에서 빠진다.
INSERT INTO public.relationship_evidence
    (relationship_id, document_id, evidence_id, contribution_score, model_version, formula_version)
SELECT cr.relationship_id, m.document_id, r.evidence_id, r.contribution_score,
       r.model_version, r.formula_version
FROM _re r
JOIN _docmap m USING (url_hash)
JOIN public.company sc ON sc.market = r.source_market AND sc.stock_code = r.source_stock_code
JOIN public.company tc ON tc.market = r.target_market AND tc.stock_code = r.target_stock_code
JOIN public.relationship_type rt ON rt.code = r.relationship_type_code
JOIN public.company_relationship cr
       ON cr.source_company_id = sc.company_id
      AND cr.target_company_id = tc.company_id
      AND cr.relationship_type_id = rt.relationship_type_id
JOIN public.document_evidence de
       ON de.evidence_id = r.evidence_id AND de.document_id = m.document_id
ON CONFLICT ON CONSTRAINT uk_relationship_evidence_identity DO NOTHING;

-- 7) 커밋 전 확인. 넣으려던 것과 실제로 DB 에 있는 것을 나란히 본다.
SELECT '문서' AS 항목, (SELECT count(*) FROM _doc) AS 입력,
       (SELECT count(*) FROM public.source_document s JOIN _docmap m USING (document_id)) AS DB
UNION ALL SELECT '기사', (SELECT count(*) FROM _art),
       (SELECT count(*) FROM public.news_article n JOIN _docmap m USING (document_id))
UNION ALL SELECT '문서-기업', (SELECT count(*) FROM _cd),
       (SELECT count(*) FROM public.company_document cd JOIN _docmap m USING (document_id)
         JOIN public.company co ON co.company_id = cd.company_id
         JOIN _cd c ON c.url_hash = m.url_hash
                   AND c.market = co.market AND c.stock_code = co.stock_code)
UNION ALL SELECT '근거 문장', (SELECT count(*) FROM _de),
       (SELECT count(*) FROM public.document_evidence de JOIN _de e USING (evidence_id))
UNION ALL SELECT '관계-근거', (SELECT count(*) FROM _re),
       (SELECT count(*) FROM public.relationship_evidence);

-- 백엔드 근거 조회가 실제로 통과하는 사슬인지 확인한다 (뉴스·발행일 필터 포함).
SELECT count(*) AS 조회가능_근거, count(DISTINCT re.relationship_id) AS 근거붙은_관계
FROM public.relationship_evidence re
JOIN public.source_document sd ON sd.document_id = re.document_id
JOIN public.news_article na ON na.document_id = sd.document_id
LEFT JOIN public.document_evidence de ON de.evidence_id = re.evidence_id
WHERE sd.document_type = 'NEWS' AND sd.published_at IS NOT NULL;

COMMIT;
