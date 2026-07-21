CREATE TABLE IF NOT EXISTS midday_v22_afternoon_run (
    id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL UNIQUE, midday_run_id VARCHAR(64) NOT NULL,
    trade_date DATE NOT NULL, recheck_time DATETIME NOT NULL, status VARCHAR(40) NOT NULL,
    current_stage VARCHAR(40) NOT NULL, previous_midday_regime VARCHAR(32), afternoon_regime VARCHAR(32),
    counts_json JSON NOT NULL, provider_audit_json JSON NOT NULL, output_paths_json JSON NOT NULL,
    failure_stage VARCHAR(40), error_code VARCHAR(80), error_message TEXT, completed_at DATETIME,
    real_orders INTEGER NOT NULL DEFAULT 0, virtual_orders INTEGER NOT NULL DEFAULT 0,
    scheduler_enabled BOOLEAN NOT NULL DEFAULT 0, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_midday_v22_afternoon_trade_status ON midday_v22_afternoon_run (trade_date, status);
CREATE TABLE IF NOT EXISTS midday_v22_afternoon_result (
    id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL, stock_code VARCHAR(32) NOT NULL,
    stock_name VARCHAR(128), prior_layer VARCHAR(40) NOT NULL, result_layer VARCHAR(40) NOT NULL,
    trigger_status VARCHAR(40) NOT NULL, payload_json JSON NOT NULL,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
    CONSTRAINT uq_midday_v22_afternoon_result UNIQUE (run_id, stock_code)
);
