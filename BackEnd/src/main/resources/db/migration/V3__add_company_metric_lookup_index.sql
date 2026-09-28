CREATE INDEX ix_company_metric_history_company_window_time
    ON company_metric_history (company_id, window_type, measured_at DESC);
