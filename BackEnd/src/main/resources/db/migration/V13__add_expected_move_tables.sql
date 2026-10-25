-- NEXUS 예상 변동폭 (2026-10-25): 뉴스 뒤 종목이 방향과 무관하게 얼마나 크게 움직였는지와 그 검증. 쓰기는 nexus cases 가 한다.
-- 방향 딱지는 시험 기간 적중률이 기준선보다 낮았지만, 크기는 통계 기간 값으로 시험 기간을 잘 구분했다.

-- 종목 × 투자 기간(SHORT 3거래일 / LONG 20거래일): 통계 기간 뉴스일 뒤 |초과수익률| 평균과, 종목을 셋으로 나눈 등급
CREATE TABLE company_expected_move (
    value_chain    VARCHAR(20) NOT NULL,
    company_id     UUID NOT NULL REFERENCES company (company_id),
    horizon        VARCHAR(5) NOT NULL CHECK (horizon IN ('SHORT', 'LONG')),
    expected_move  NUMERIC(12, 8) NOT NULL,
    case_count     INTEGER NOT NULL,
    grade          VARCHAR(5) NOT NULL CHECK (grade IN ('SMALL', 'MID', 'LARGE')),
    period_end     DATE NOT NULL,
    PRIMARY KEY (value_chain, company_id, horizon)
);
CREATE INDEX ix_company_expected_move_company ON company_expected_move (company_id, horizon);

-- 검증: 통계 기간 뒤 뉴스일에서 등급별 예상 변동폭 평균과 실제 |초과수익률| 평균
CREATE TABLE move_backtest (
    backtest_id     BIGSERIAL PRIMARY KEY,
    value_chain     VARCHAR(20) NOT NULL,
    horizon         VARCHAR(5) NOT NULL CHECK (horizon IN ('SHORT', 'LONG')),
    grade           VARCHAR(5) NOT NULL CHECK (grade IN ('SMALL', 'MID', 'LARGE')),
    case_count      INTEGER NOT NULL,
    predicted_move  NUMERIC(12, 8),
    realized_move   NUMERIC(12, 8),
    train_end       DATE NOT NULL,
    test_start      DATE NOT NULL,
    test_end        DATE NOT NULL,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
