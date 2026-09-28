-- 신규 그래프 집계는 30D, 1Y, 10Y를 사용한다.
-- 기존 7D, 90D 이력은 삭제하지 않고 계속 보존할 수 있도록 허용한다.
ALTER TABLE company_metric_history
    DROP CONSTRAINT chk_company_metric_history_window_type;

ALTER TABLE company_metric_history
    ADD CONSTRAINT chk_company_metric_history_window_type
        CHECK (window_type IN ('7D', '30D', '90D', '1Y', '10Y'));

-- 기존 적재 영수증과 신규 적재 영수증을 모두 유효하게 유지한다.
ALTER TABLE graph_snapshot_load
    DROP CONSTRAINT chk_graph_snapshot_load_windows;

ALTER TABLE graph_snapshot_load
    ADD CONSTRAINT chk_graph_snapshot_load_windows
        CHECK (
            windows = ARRAY['7D', '30D', '90D']::TEXT[]
            OR windows = ARRAY['30D', '1Y', '10Y']::TEXT[]
        );
