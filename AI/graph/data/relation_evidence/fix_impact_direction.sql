-- impact_direction 을 설계대로 되돌린다.
--
--   psql -v ON_ERROR_STOP=1 -f fix_impact_direction.sql
--
-- 설계 (build_expanded_relation_seed.py 머리말)
--   relationship_type 은 근거 문장이 말하는 관계, impact_direction 은 가격이 실제로
--   같이 움직인 방향이다. 둘은 독립이다. 부호는 estimate_edge_direction.py 의
--   쌍별 추정을 (무순서 쌍, 유형) 키로 찾고, 없으면 POSITIVE 로 둔다.
--
-- 깨져 있던 것 두 가지
--   1) 확장 로더가 키를 frozenset(쌍) 으로 잡아 유형을 버렸다. 한 쌍에 여러 유형이
--      있으면 부호가 서로 번진다. 실측: 005490-003670 의 INVEST(r=-0.0568) 부호가
--      SUPPLY 에 붙어 같은 그룹 계열사가 화면에 '부정 전이' 로 떴다.
--   2) 영문 시드가 "POSITIVE" 를 리터럴로 넣었다. 그 표에 해외 종목이 아예 없어
--      재본 적이 없는데도 54관계 전부 '긍정 전이' 로 표시됐다.
--
-- relationship_impact_direction.csv 는 data/db/ 의 것과 같은 파일이다. psql 의 \copy 가
-- 작업 디렉터리 기준이라 여기 같이 둔다. 원본이 바뀌면 이 복사본도 다시 맞춰야 한다.
--
-- 실측 영향 (국내 193관계)
--   NEGATIVE -> POSITIVE  5개, 나머지 188개 그대로. 영문 54관계는 POSITIVE -> NULL.
--   바뀌는 5개가 전부 같은 그룹 계열사다 — 포스코퓨처엠/POSCO홀딩스, 삼성중공업/삼성전자,
--   삼성E&A/삼성SDI, 현대로템/현대차, 기아/현대건설. 4개가 SUPPLY 인데 SUPPLY 는
--   부호표에 행이 없어 같은 쌍의 INVEST/PARTNER 부호를 물려받고 있었다.
--
-- 이 스크립트는 관계·점수·근거를 건드리지 않는다. impact_direction 칸만 다시 쓴다.
-- 멱등하다.

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE _t AS SELECT run_id FROM cosmos_analysis.active_run;

CREATE TEMP TABLE _dir (source_market text, source_stock_code text, target_market text,
                        target_stock_code text, relationship_type_code text,
                        impact_direction text, source text,
                        corr_train numeric, n_train int) ON COMMIT DROP;
\copy _dir FROM 'relationship_impact_direction.csv' WITH (FORMAT csv, HEADER true)

-- 고치기 전 상태를 남긴다
SELECT '고치기 전' AS 단계, formula_version,
       count(*) FILTER (WHERE impact_direction = 'POSITIVE') AS 긍정,
       count(*) FILTER (WHERE impact_direction = 'NEGATIVE') AS 부정,
       count(*) FILTER (WHERE impact_direction IS NULL)      AS 미측정
FROM cosmos_analysis.relationship_score_current
WHERE run_id = (SELECT run_id FROM _t) AND window_type = '30D'
GROUP BY 1, 2 ORDER BY 2;

-- 1) 영문 관계: 재본 적이 없으므로 NULL 로 되돌린다.
--    화면에 '분석 전' 으로 뜨는 편이 측정하지 않은 'POSITIVE' 보다 정직하다.
UPDATE cosmos_analysis.relationship_score_current s
   SET impact_direction = NULL
  FROM _t
 WHERE s.run_id = _t.run_id
   AND s.formula_version = 'rel-v0.4-en-news';

-- 2) 국내 관계: (무순서 쌍, 유형) 키로 다시 찾는다. 표에 없으면 POSITIVE.
--    전부 POSITIVE 가 파라미터 0개 기준선이고 test 63.5% 로 유형 기본값(57.4%)보다 낫다.
UPDATE cosmos_analysis.relationship_score_current s
   SET impact_direction = COALESCE(d.impact_direction, 'POSITIVE')
  FROM _t,
       cosmos_analysis.company_relationship cr
       JOIN company sc ON sc.company_id = cr.source_company_id
       JOIN company tc ON tc.company_id = cr.target_company_id
       JOIN relationship_type rt ON rt.relationship_type_id = cr.relationship_type_id
       LEFT JOIN _dir d
              ON LEAST(d.source_stock_code, d.target_stock_code)
                 = LEAST(sc.stock_code, tc.stock_code)
             AND GREATEST(d.source_stock_code, d.target_stock_code)
                 = GREATEST(sc.stock_code, tc.stock_code)
             AND d.relationship_type_code = rt.code
 WHERE s.run_id = _t.run_id
   AND cr.run_id = _t.run_id
   AND cr.relationship_id = s.relationship_id
   AND s.formula_version <> 'rel-v0.4-en-news';

SELECT '고친 뒤' AS 단계, formula_version,
       count(*) FILTER (WHERE impact_direction = 'POSITIVE') AS 긍정,
       count(*) FILTER (WHERE impact_direction = 'NEGATIVE') AS 부정,
       count(*) FILTER (WHERE impact_direction IS NULL)      AS 미측정
FROM cosmos_analysis.relationship_score_current
WHERE run_id = (SELECT run_id FROM _t) AND window_type = '30D'
GROUP BY 1, 2 ORDER BY 2;

-- 문제로 지목된 쌍을 직접 확인한다
SELECT sc.name AS 출발, tc.name AS 도착, rt.code AS 유형, s.impact_direction AS 방향
FROM cosmos_analysis.relationship_score_current s
JOIN _t ON _t.run_id = s.run_id
JOIN cosmos_analysis.company_relationship cr
       ON cr.run_id = s.run_id AND cr.relationship_id = s.relationship_id
JOIN company sc ON sc.company_id = cr.source_company_id
JOIN company tc ON tc.company_id = cr.target_company_id
JOIN relationship_type rt ON rt.relationship_type_id = cr.relationship_type_id
WHERE s.window_type = '30D'
  AND (sc.stock_code IN ('003670','005490') OR tc.stock_code IN ('003670','005490')
       OR sc.stock_code IN ('AMZN','MSFT') OR tc.stock_code IN ('AMZN','MSFT'))
ORDER BY 1, 2;

COMMIT;
