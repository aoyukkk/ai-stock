CREATE TABLE IF NOT EXISTS midday_v22_run (
 id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL UNIQUE, trade_date DATE NOT NULL, cutoff_time DATETIME NOT NULL,
 run_mode VARCHAR(40) NOT NULL, status VARCHAR(40) NOT NULL, current_stage VARCHAR(40) NOT NULL,
 previous_regime VARCHAR(32), midday_regime VARCHAR(32), input_hash VARCHAR(64) NOT NULL, config_hash VARCHAR(64) NOT NULL,
 quant_hash VARCHAR(64), flash_hash VARCHAR(64), pro_hash VARCHAR(64), output_hash VARCHAR(64), counts_json JSON NOT NULL,
 provider_audit_json JSON NOT NULL, checkpoint_json JSON NOT NULL, warnings_json JSON NOT NULL, failure_stage VARCHAR(40),
 error_code VARCHAR(80), error_message TEXT, output_paths_json JSON NOT NULL, real_orders INTEGER NOT NULL DEFAULT 0,
 virtual_orders INTEGER NOT NULL DEFAULT 0, scheduler_enabled BOOLEAN NOT NULL DEFAULT 0, completed_at DATETIME,
 created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_midday_v22_trade_status ON midday_v22_run(trade_date,status);
CREATE INDEX IF NOT EXISTS ix_midday_v22_input_hash ON midday_v22_run(input_hash);
CREATE TABLE IF NOT EXISTS midday_v22_result (
 id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL, stock_code VARCHAR(32) NOT NULL, stock_name VARCHAR(128),
 pool_type VARCHAR(32) NOT NULL, result_layer VARCHAR(32) NOT NULL, payload_json JSON NOT NULL,
 created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, UNIQUE(run_id,stock_code,pool_type)
);
