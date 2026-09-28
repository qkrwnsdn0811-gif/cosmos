-- 관계 신뢰도를 근거의 양과 질로 다시 계산한다.
--
--   psql -v ON_ERROR_STOP=1 -f recompute_confidence.sql
--
-- 왜 SQL 인가
--   출처 종류·언론사·발행일이 전부 DB 에 있다. 시드 스크립트 두 개에 나눠 넣으면
--   국내분과 영문분의 기준이 갈린다. 한 곳에서 모든 관계에 같은 식을 적용한다.
--
-- 이전 식 (0.5 + 0.1n) 의 문제
--   근거 5건에서 이미 100% 라 245개 중 28%가 만점에 몰렸다. 근거 5건과 150건이
--   같은 값이었다. 그리고 근거 1건인 105개(43%)가 전부 60% 로 동일했다 —
--   법정 공시 1건과 뉴스 한 문장이 구분되지 않았다.
--
-- 새 식
--   base = 0.50 + 0.35 * (1 - exp(-근거수 / 8))          0.50 ~ 0.85
--        + 0.05  공시 근거 포함        (DART 는 법정 공시다)
--        + 0.05  뉴스와 공시 둘 다     (독립된 두 출처가 같은 관계를 지지)
--        + 0.03  언론사 2곳 이상       (한 기사 받아쓰기와 독립 보도는 다르다)
--        + 0.02  근거 기간 180일 이상  (지속되는 관계)
--   상한 1.0
--
-- 실측 분포 (245관계)
--   공시 포함 125 · 뉴스+공시 22 · 언론사 2곳 이상 36 · 기간 180일+ 102
--
-- ⚠️ 확률이 아니다. 가중치 0.05/0.05/0.03/0.02 는 검증된 값이 아니라 설계 판단이다.
--    "공시가 뉴스보다 근거가 강하다"는 타당하지만 그 차이가 5%p 라는 근거는 없다.
--    화면에 '신뢰도 N%' 로 표기하면 검증된 확률로 오해된다. 표기 변경을 권한다.

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE _t AS SELECT run_id FROM cosmos_analysis.active_run;

CREATE TEMP TABLE _sig ON COMMIT DROP AS
SELECT s.relationship_id,
       count(DISTINCT re.document_id)                                          AS n_doc,
       bool_or(d.document_type = 'DISCLOSURE')                                 AS has_disc,
       bool_or(d.document_type = 'DISCLOSURE') AND bool_or(d.document_type = 'NEWS') AS has_both,
       count(DISTINCT na.publisher) >= 2                                       AS multi_pub,
       COALESCE(max(d.published_at)::date - min(d.published_at)::date, 0) >= 180 AS long_span
FROM cosmos_analysis.relationship_score_current s
JOIN _t ON _t.run_id = s.run_id
LEFT JOIN cosmos_analysis.relationship_evidence re
       ON re.run_id = s.run_id AND re.relationship_id = s.relationship_id
LEFT JOIN cosmos_analysis.source_document d
       ON d.run_id = s.run_id AND d.document_id = re.document_id
LEFT JOIN cosmos_analysis.news_article na
       ON na.run_id = s.run_id AND na.document_id = d.document_id
WHERE s.window_type = '30D'
GROUP BY 1;

UPDATE cosmos_analysis.relationship_score_current s
   SET confidence = LEAST(1.0, ROUND((
           0.50 + 0.35 * (1 - exp(-g.n_doc::numeric / 8))
         + CASE WHEN g.has_disc  THEN 0.05 ELSE 0 END
         + CASE WHEN g.has_both  THEN 0.05 ELSE 0 END
         + CASE WHEN g.multi_pub THEN 0.03 ELSE 0 END
         + CASE WHEN g.long_span THEN 0.02 ELSE 0 END)::numeric, 6))
  FROM _sig g, _t
 WHERE s.run_id = _t.run_id AND s.relationship_id = g.relationship_id
   AND s.window_type = '30D' AND g.n_doc > 0;

SELECT count(*) AS 관계,
       round(min(confidence)*100)    AS 최저,
       round(avg(confidence)*100)    AS 평균,
       round(max(confidence)*100)    AS 최고,
       count(*) FILTER (WHERE confidence >= 0.99) AS 만점
FROM cosmos_analysis.relationship_score_current s
JOIN _t ON _t.run_id = s.run_id
WHERE s.window_type = '30D' AND s.confidence IS NOT NULL;

COMMIT;
