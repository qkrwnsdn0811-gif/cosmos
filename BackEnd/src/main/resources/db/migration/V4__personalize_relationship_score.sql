-- 관계 점수 개인화 정책 변경 (2026-09-15 팀 합의)
-- 1) 사용자 가중치는 서버에 저장하지 않는다 → user_weight_setting 삭제
-- 2) 관계 점수를 뉴스분/공시분으로 나눠 함께 저장하고, 프론트가 비율을 조합한다
--    - news_score / disclosure_score : 출처별 구성 점수. 해당 출처의 근거가 없으면 0이 아닌 NULL
--    - score                          : 뉴스 50%·공시 50% 기본 비율로 합친 공통 점수 (기존 컬럼 유지)

DROP TABLE IF EXISTS user_weight_setting;

ALTER TABLE relationship_score_current
    ADD COLUMN news_score        NUMERIC(18, 6),
    ADD COLUMN disclosure_score  NUMERIC(18, 6);

ALTER TABLE relationship_score_history
    ADD COLUMN news_score        NUMERIC(18, 6),
    ADD COLUMN disclosure_score  NUMERIC(18, 6);

COMMENT ON COLUMN relationship_score_current.news_score
    IS '뉴스 근거로 계산한 관계 점수. 뉴스 근거가 없으면 NULL';
COMMENT ON COLUMN relationship_score_current.disclosure_score
    IS '공시 근거로 계산한 관계 점수. 공시 근거가 없으면 NULL';
COMMENT ON COLUMN relationship_score_current.score
    IS '뉴스 50%·공시 50% 기본 비율로 합친 공통 관계 점수';

COMMENT ON COLUMN relationship_score_history.news_score
    IS '뉴스 근거로 계산한 관계 점수. 뉴스 근거가 없으면 NULL';
COMMENT ON COLUMN relationship_score_history.disclosure_score
    IS '공시 근거로 계산한 관계 점수. 공시 근거가 없으면 NULL';
COMMENT ON COLUMN relationship_score_history.score
    IS '뉴스 50%·공시 50% 기본 비율로 합친 공통 관계 점수';
