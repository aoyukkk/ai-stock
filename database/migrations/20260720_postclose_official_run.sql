CREATE TABLE IF NOT EXISTS postclose_official_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(80) NOT NULL UNIQUE,
    trade_date DATE NOT NULL,
    status VARCHAR(64) NOT NULL,
    stage VARCHAR(64) NOT NULL,
    started_at DATETIME NOT NULL,
    completed_at DATETIME,
    report_json JSON NOT NULL DEFAULT '{}',
    output_paths_json JSON NOT NULL DEFAULT '{}',
    error_code VARCHAR(128),
    error_message TEXT,
    provider_calls INTEGER NOT NULL DEFAULT 0,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    per_stock_api_calls INTEGER NOT NULL DEFAULT 0,
    real_orders INTEGER NOT NULL DEFAULT 0,
    virtual_orders INTEGER NOT NULL DEFAULT 0,
    scheduler_enabled BOOLEAN NOT NULL DEFAULT 0,
    production_config_changed BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_postclose_official_trade_date
ON postclose_official_run (trade_date, status);
