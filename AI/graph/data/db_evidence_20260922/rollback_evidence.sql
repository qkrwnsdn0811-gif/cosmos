-- load_evidence.sql 을 되돌린다. 이번에 넣은 행만 지운다.
--
--   docker exec -w /tmp/evi -i cosmos-postgres-1 psql -U cosmos -d cosmos \
--     -v ON_ERROR_STOP=1 -f rollback_evidence.sql
--
-- 기준은 model_version = 'dict-v1.3+finbert-evidence-v1' 이다. 수집기가 넣은 행은
-- 다른 model_version 을 쓰므로 건드리지 않는다. source_document/news_article 은
-- 이번에 새로 생긴 것(= 남은 참조가 하나도 없는 것)만 지운다.

\set ON_ERROR_STOP on
BEGIN;

-- 이번에 채운 발행일을 다시 비운다 (원래 NULL 이던 것만 적어 뒀다).
UPDATE public.source_document s SET published_at = NULL, updated_at = CURRENT_TIMESTAMP
FROM public.source_document_published_fill_20260922 f
WHERE s.document_id = f.document_id;
DROP TABLE IF EXISTS public.source_document_published_fill_20260922;

DELETE FROM public.relationship_evidence
WHERE model_version = 'dict-v1.3+finbert-evidence-v1';

DELETE FROM public.document_evidence
WHERE model_version = 'dict-v1.3+finbert-evidence-v1';

DELETE FROM public.company_document
WHERE model_version = 'dict-v1.3+finbert-evidence-v1' AND is_service_visible = false;

-- 우리가 만든 아카이브 출처의 문서 중, 아무 데서도 안 쓰는 것
DELETE FROM public.news_article n
USING public.source_document s, public.data_source ds
WHERE n.document_id = s.document_id AND ds.source_id = s.source_id
  AND ds.name LIKE 'news:archive:%'
  AND NOT EXISTS (SELECT 1 FROM public.company_document c WHERE c.document_id = s.document_id)
  AND NOT EXISTS (SELECT 1 FROM public.relationship_evidence r WHERE r.document_id = s.document_id);

DELETE FROM public.source_document s
USING public.data_source ds
WHERE ds.source_id = s.source_id AND ds.name LIKE 'news:archive:%'
  AND NOT EXISTS (SELECT 1 FROM public.company_document c WHERE c.document_id = s.document_id)
  AND NOT EXISTS (SELECT 1 FROM public.relationship_evidence r WHERE r.document_id = s.document_id)
  AND NOT EXISTS (SELECT 1 FROM public.news_article n WHERE n.document_id = s.document_id);

DELETE FROM public.data_source ds
WHERE ds.name LIKE 'news:archive:%'
  AND NOT EXISTS (SELECT 1 FROM public.source_document s WHERE s.source_id = ds.source_id);

SELECT (SELECT count(*) FROM public.relationship_evidence) AS 관계근거,
       (SELECT count(*) FROM public.document_evidence)     AS 근거문장,
       (SELECT count(*) FROM public.source_document)       AS 문서;

COMMIT;
