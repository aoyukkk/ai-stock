CREATE TABLE IF NOT EXISTS admission_run (
  id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL UNIQUE, trade_date DATE NOT NULL,
  quant_run_id VARCHAR(64) NOT NULL, input_hash VARCHAR(64) NOT NULL UNIQUE,
  config_snapshot JSON NOT NULL, candidate_count INTEGER NOT NULL DEFAULT 0,
  pass_count INTEGER NOT NULL DEFAULT 0, review_count INTEGER NOT NULL DEFAULT 0,
  block_count INTEGER NOT NULL DEFAULT 0, insufficient_count INTEGER NOT NULL DEFAULT 0,
  admitted_count INTEGER NOT NULL DEFAULT 0, manual_challenge_count INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(32) NOT NULL, shadow_only BOOLEAN NOT NULL DEFAULT 1,
  enabled_in_production BOOLEAN NOT NULL DEFAULT 0, quant_hash_before VARCHAR(64) NOT NULL,
  quant_hash_after VARCHAR(64) NOT NULL, llm_call_count INTEGER NOT NULL DEFAULT 0,
  external_api_call_count INTEGER NOT NULL DEFAULT 0, completed_at DATETIME,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_admission_run_trade_status ON admission_run(trade_date, status);
CREATE UNIQUE INDEX IF NOT EXISTS ix_admission_run_input_hash ON admission_run(input_hash);

CREATE TABLE IF NOT EXISTS entry_timing_result (
  id INTEGER PRIMARY KEY, admission_run_id VARCHAR(64) NOT NULL, trade_date DATE NOT NULL,
  stock_code VARCHAR(32) NOT NULL, stock_name VARCHAR(128), quant_run_id VARCHAR(64) NOT NULL,
  quant_rank INTEGER, quant_score NUMERIC(10,4) NOT NULL, flash_score NUMERIC(10,4),
  position_score NUMERIC(10,4) NOT NULL, pullback_score NUMERIC(10,4) NOT NULL,
  volume_price_score NUMERIC(10,4) NOT NULL, sector_score NUMERIC(10,4) NOT NULL,
  market_score NUMERIC(10,4) NOT NULL, liquidity_score NUMERIC(10,4) NOT NULL,
  entry_timing_score NUMERIC(10,4) NOT NULL, data_quality_score NUMERIC(10,4) NOT NULL,
  admission_status VARCHAR(32) NOT NULL, risk_flags JSON NOT NULL, block_reasons JSON NOT NULL,
  pool_type VARCHAR(32) NOT NULL, manual_score NUMERIC(10,4), ai_score NUMERIC(10,4),
  score_difference NUMERIC(10,4), diagnostics JSON NOT NULL, config_version VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  UNIQUE(admission_run_id, stock_code, pool_type)
);
CREATE INDEX IF NOT EXISTS ix_entry_timing_trade_status ON entry_timing_result(trade_date, admission_status);
CREATE INDEX IF NOT EXISTS ix_entry_timing_run_score ON entry_timing_result(admission_run_id, entry_timing_score);
