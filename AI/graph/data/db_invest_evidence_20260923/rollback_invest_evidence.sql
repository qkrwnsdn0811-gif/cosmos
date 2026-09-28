-- load_invest_evidence.sql 을 되돌린다. 이번에 넣은 행만 지운다.
--
--   docker exec -w /tmp/inv -i cosmos-postgres-1 psql -U cosmos -d cosmos \
--     -v ON_ERROR_STOP=1 -f rollback_invest_evidence.sql
--
-- 기준은 model_version = 'dart-ownership-gemini-2.5-flash-lite' 다.
-- 뉴스 근거(dict-v1.3+finbert-evidence-v1)와 수집기 행은 건드리지 않는다.

\set ON_ERROR_STOP on
BEGIN;

DELETE FROM public.relationship_evidence
WHERE model_version = 'dart-ownership-gemini-2.5-flash-lite';

DELETE FROM public.document_evidence
WHERE model_version = 'dart-ownership-gemini-2.5-flash-lite';

-- 이번에 만든 문서-기업 행만. 수집기가 넣은 FILER 행은 다른 mention_type 이다.
DELETE FROM public.company_document
WHERE model_version = 'dart-ownership-gemini-2.5-flash-lite'
  AND mention_type = 'MENTION' AND is_service_visible = false;

SELECT (SELECT count(*) FROM public.relationship_evidence) AS 관계근거,
       (SELECT count(*) FROM public.document_evidence)     AS 근거문장;

COMMIT;
