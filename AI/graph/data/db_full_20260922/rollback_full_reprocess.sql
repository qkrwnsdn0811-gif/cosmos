-- load_full_reprocess.sql 을 되돌린다.
--
--   docker exec -i cosmos-postgres-1 psql -U cosmos -d cosmos \
--     -v ON_ERROR_STOP=1 -f rollback_full_reprocess.sql
--
-- 적재 직전 상태를 통째로 떠 둔 relationship_score_current_bak_20260922 로 되돌린다.
-- company_relationship 행은 점수·근거가 하나도 안 남은 것만 지운다.

\set ON_ERROR_STOP on
BEGIN;

DO $guard$
BEGIN
    IF to_regclass('public.relationship_score_current_bak_20260922') IS NULL THEN
        RAISE EXCEPTION '백업 테이블이 없다 — 되돌릴 수 없다';
    END IF;
END $guard$;

DELETE FROM public.relationship_score_current;
INSERT INTO public.relationship_score_current
SELECT * FROM public.relationship_score_current_bak_20260922;

DELETE FROM public.graph_snapshot
WHERE formula_version = 'rel-v0.5-full'
  AND NOT EXISTS (SELECT 1 FROM public.relationship_score_current s
                   WHERE s.snapshot_id = graph_snapshot.snapshot_id)
  AND NOT EXISTS (SELECT 1 FROM public.relationship_score_history h
                   WHERE h.snapshot_id = graph_snapshot.snapshot_id)
  AND NOT EXISTS (SELECT 1 FROM public.graph_snapshot_load l
                   WHERE l.snapshot_id = graph_snapshot.snapshot_id);

DELETE FROM public.company_relationship cr
WHERE NOT EXISTS (SELECT 1 FROM public.relationship_score_current s
                   WHERE s.relationship_id = cr.relationship_id)
  AND NOT EXISTS (SELECT 1 FROM public.relationship_evidence e
                   WHERE e.relationship_id = cr.relationship_id);

SELECT g.as_of_at, g.formula_version, s.window_type, count(*)
FROM public.relationship_score_current s
JOIN public.graph_snapshot g ON g.snapshot_id = s.snapshot_id
GROUP BY 1, 2, 3 ORDER BY 1, 3;

COMMIT;
