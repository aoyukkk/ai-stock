CREATE TABLE IF NOT EXISTS midday_full_a_radar_run (
  id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(80) NOT NULL UNIQUE, trade_date DATE NOT NULL,
  cutoff_time DATETIME NOT NULL, run_mode VARCHAR(48) NOT NULL, status VARCHAR(48) NOT NULL,
  stage VARCHAR(48) NOT NULL, input_hash VARCHAR(64) NOT NULL UNIQUE, config_hash VARCHAR(64) NOT NULL,
  universe_hash VARCHAR(64), asof_data_hash VARCHAR(64), baseline_quant_hash VARCHAR(64), radar_config_hash VARCHAR(64),
  radar_output_hash VARCHAR(64), regime_hash VARCHAR(64), rule_output_hash VARCHAR(64), llm_output_hash VARCHAR(64), export_hash VARCHAR(64),
  current_git_head VARCHAR(64), counts_json JSON NOT NULL DEFAULT '{}', audit_json JSON NOT NULL DEFAULT '[]',
  report_json JSON NOT NULL DEFAULT '{}', output_paths_json JSON NOT NULL DEFAULT '{}', error_code VARCHAR(96), error_message TEXT,
  execution_started_at DATETIME NOT NULL, execution_completed_at DATETIME, real_orders INTEGER NOT NULL DEFAULT 0,
  virtual_orders INTEGER NOT NULL DEFAULT 0, scheduler_enabled BOOLEAN NOT NULL DEFAULT 0, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS midday_full_a_radar_result (
  id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(80) NOT NULL, stock_code VARCHAR(32) NOT NULL, stock_name VARCHAR(128), industry VARCHAR(128),
  midday_rank INTEGER NOT NULL, industry_rank INTEGER, baseline_quant_score FLOAT NOT NULL, morning_relative_strength FLOAT,
  morning_volume_price FLOAT, sector_resonance FLOAT, opening_risk_quality FLOAT, midday_radar_score FLOAT NOT NULL,
  data_quality VARCHAR(32) NOT NULL, score_version VARCHAR(64) NOT NULL, risk_flags_json JSON NOT NULL DEFAULT '[]',
  payload_json JSON NOT NULL DEFAULT '{}', created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  CONSTRAINT uq_midday_full_a_run_stock UNIQUE (run_id, stock_code)
);
