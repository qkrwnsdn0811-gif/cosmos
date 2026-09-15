-- 기간별 기업 지표를 같은 계산 시각에도 구분하여 저장한다.
ALTER TABLE company_metric_history
    ADD COLUMN window_type VARCHAR(30) NOT NULL DEFAULT '30D';

ALTER TABLE company_metric_history
    DROP CONSTRAINT company_metric_history_pkey;

ALTER TABLE company_metric_history
    ADD PRIMARY KEY (company_id, measured_at, window_type);

ALTER TABLE company_metric_history
    ADD CONSTRAINT chk_company_metric_history_window_type
        CHECK (window_type IN ('7D', '30D', '90D'));

ALTER TABLE company_metric_history
    ALTER COLUMN window_type DROP DEFAULT;
