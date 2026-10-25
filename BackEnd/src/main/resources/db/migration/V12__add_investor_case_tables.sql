-- NEXUS 투자자용 기능 (2026-10-25): 뉴스 유형(F2), 과거 사례 반응표(F3), 방향 딱지(F4), 적중률(F11),
-- 뉴스 전후 주가 차트용 시장 지수(F7), 기업 지표(F8). 쓰기는 파이프라인(nexus cases / nexus fundamentals)이 한다.
-- 딱지는 모델 예측이 아니라 학습 기간(2025-01 ~ 2026-06) 과거 사례 통계이고, 적중률은 그 뒤 기간으로 잰다.

-- 초과수익률 기간에 5·20거래일을 더한다 (단기 3거래일, 장기 20거래일 딱지와 차트 표시)
ALTER TABLE event_abnormal_return DROP CONSTRAINT event_abnormal_return_window_days_check;
ALTER TABLE event_abnormal_return
    ADD CONSTRAINT event_abnormal_return_window_days_check CHECK (window_days IN (1, 3, 5, 20));

-- 뉴스 유형은 V10의 news_event.event_type / event_type_confidence 를 그대로 쓴다 (코드는 nexus/nexus/cases/newstype.py)
CREATE INDEX ix_news_event_type ON news_event (value_chain, event_type);

-- 과거 사례 반응표: (유형, 기업 위치[, 기업]) × 기간. company_id 가 NULL 이면 같은 위치 기업 전체
CREATE TABLE case_reaction_stat (
    stat_id       BIGSERIAL PRIMARY KEY,
    value_chain   VARCHAR(20) NOT NULL,
    news_type     VARCHAR(30) NOT NULL,
    position      VARCHAR(20) NOT NULL
        CHECK (position IN ('REPORTED', 'SUPPLIER', 'CUSTOMER', 'COMPETITOR', 'PARTNER', 'INVEST', 'COMENTION')),
    company_id    UUID REFERENCES company (company_id),
    window_days   SMALLINT NOT NULL CHECK (window_days IN (1, 3, 5, 20)),
    case_count    INTEGER NOT NULL,
    mean_ar       NUMERIC(12, 8) NOT NULL,
    median_ar     NUMERIC(12, 8) NOT NULL,
    down_count    INTEGER NOT NULL,
    up_count      INTEGER NOT NULL,
    period_start  DATE NOT NULL,
    period_end    DATE NOT NULL
);
CREATE UNIQUE INDEX uk_case_reaction_stat
    ON case_reaction_stat (value_chain, news_type, position, COALESCE(company_id, '00000000-0000-0000-0000-000000000000'::uuid), window_days);

-- 이벤트 × 기업 × 투자 기간(SHORT 3거래일 / LONG 20거래일)의 방향 딱지와 그 근거가 된 과거 사례 수치
CREATE TABLE event_company_signal (
    event_id      UUID NOT NULL REFERENCES news_event (event_id) ON DELETE CASCADE,
    company_id    UUID NOT NULL REFERENCES company (company_id),
    horizon       VARCHAR(5) NOT NULL CHECK (horizon IN ('SHORT', 'LONG')),
    position      VARCHAR(20) NOT NULL,
    label         VARCHAR(4) NOT NULL CHECK (label IN ('UP', 'DOWN', 'HOLD')),
    hold_reason   VARCHAR(50),
    case_scope    VARCHAR(10) CHECK (case_scope IN ('COMPANY', 'POSITION')),
    case_count    INTEGER NOT NULL DEFAULT 0,
    same_share    NUMERIC(7, 6),
    mean_ar       NUMERIC(12, 8),
    down_share    NUMERIC(7, 6),
    PRIMARY KEY (event_id, company_id, horizon),
    CONSTRAINT chk_signal_hold_reason CHECK (label <> 'HOLD' OR hold_reason IS NOT NULL)
);
CREATE INDEX ix_event_company_signal_company ON event_company_signal (company_id, horizon);

-- 적중률(F11): 학습 기간 뒤 뉴스에서 딱지 방향이 실제 초과수익률 부호와 맞은 비율. news_type 이 NULL 이면 전체
CREATE TABLE signal_backtest (
    backtest_id      BIGSERIAL PRIMARY KEY,
    value_chain      VARCHAR(20) NOT NULL,
    horizon          VARCHAR(5) NOT NULL CHECK (horizon IN ('SHORT', 'LONG')),
    news_type        VARCHAR(30),
    case_count       INTEGER NOT NULL,
    labeled_count    INTEGER NOT NULL,
    hit_count        INTEGER NOT NULL,
    up_labels        INTEGER NOT NULL,
    down_labels      INTEGER NOT NULL,
    base_down_share  NUMERIC(7, 6),
    train_end        DATE NOT NULL,
    test_start       DATE NOT NULL,
    test_end         DATE NOT NULL,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 뉴스 전후 주가 차트의 비교 대상(코스피·코스닥 지수 종가). 종목 주가는 stock_price_history 에 있다
CREATE TABLE market_index_price (
    index_code    VARCHAR(10) NOT NULL CHECK (index_code IN ('KOSPI', 'KOSDAQ')),
    trading_date  DATE NOT NULL,
    close_price   NUMERIC(14, 4) NOT NULL,
    PRIMARY KEY (index_code, trading_date)
);

-- 기업 지표(F8): 시가총액, PER, PBR, 배당수익률, 규모. OpenDART 재무제표 + 시가총액으로 계산
CREATE TABLE company_fundamental (
    company_id          UUID PRIMARY KEY REFERENCES company (company_id),
    market_cap          NUMERIC(20, 0),
    market_cap_rank     INTEGER,
    size_class          VARCHAR(6) CHECK (size_class IN ('LARGE', 'MID', 'SMALL')),
    net_income_ttm      NUMERIC(20, 0),
    equity              NUMERIC(20, 0),
    dividend_per_share  NUMERIC(14, 2),
    close_price         NUMERIC(14, 2),
    per                 NUMERIC(12, 4),
    pbr                 NUMERIC(12, 4),
    dividend_yield      NUMERIC(9, 6),
    fiscal_basis        VARCHAR(100),
    price_date          DATE,
    source              VARCHAR(50) NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
