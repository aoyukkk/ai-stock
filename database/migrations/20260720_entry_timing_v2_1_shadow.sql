CREATE TABLE IF NOT EXISTS admission_v2_run (
  id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL UNIQUE, trade_date DATE NOT NULL,
  quant_run_id VARCHAR(64) NOT NULL, v1_run_id VARCHAR(64) NOT NULL, input_hash VARCHAR(64) NOT NULL UNIQUE,
  config_snapshot JSON NOT NULL, candidate_count INTEGER NOT NULL, pass_count INTEGER NOT NULL,
  review_count INTEGER NOT NULL, block_count INTEGER NOT NULL, admitted_count INTEGER NOT NULL,
  manual_challenge_count INTEGER NOT NULL, market_emotion_score NUMERIC(12,4),
  market_emotion_state VARCHAR(32) NOT NULL, market_regime VARCHAR(32) NOT NULL,
  strategy_distribution JSON NOT NULL, status VARCHAR(32) NOT NULL, shadow_only BOOLEAN NOT NULL,
  enabled_in_production BOOLEAN NOT NULL, quant_hash_before VARCHAR(64) NOT NULL, quant_hash_after VARCHAR(64) NOT NULL,
  flash_hash_before VARCHAR(64) NOT NULL, flash_hash_after VARCHAR(64) NOT NULL,
  pro_hash_before VARCHAR(64) NOT NULL, pro_hash_after VARCHAR(64) NOT NULL,
  llm_call_count INTEGER NOT NULL, external_api_call_count INTEGER NOT NULL, order_creation_count INTEGER NOT NULL,
  completed_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_classification_result (
  id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL, trade_date DATE NOT NULL, stock_code VARCHAR(32) NOT NULL,
  strategy_id VARCHAR(32) NOT NULL, primary_strategy VARCHAR(32) NOT NULL, alternative_strategies_json JSON NOT NULL,
  strategy_fit_score NUMERIC(12,4) NOT NULL, pattern_fit_score NUMERIC(12,4) NOT NULL,
  regime_compatibility_score NUMERIC(12,4) NOT NULL, sector_compatibility_score NUMERIC(12,4),
  data_quality_score NUMERIC(12,4) NOT NULL, confidence NUMERIC(12,4) NOT NULL,
  matched_conditions_json JSON NOT NULL, failed_conditions_json JSON NOT NULL, status VARCHAR(32) NOT NULL,
  classifier_version VARCHAR(64) NOT NULL, details_json JSON NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  CONSTRAINT uq_strategy_classification_run_stock UNIQUE (run_id, stock_code)
);

CREATE TABLE IF NOT EXISTS market_emotion_snapshot (
  id INTEGER PRIMARY KEY, trade_date DATE NOT NULL, decision_time DATETIME NOT NULL,
  breadth_health NUMERIC(12,4), limit_structure_health NUMERIC(12,4), break_board_health NUMERIC(12,4),
  median_return_health NUMERIC(12,4), turnover_health NUMERIC(12,4), tail_risk_health NUMERIC(12,4),
  market_emotion_score NUMERIC(12,4), emotion_state VARCHAR(32) NOT NULL, market_regime VARCHAR(32) NOT NULL,
  component_coverage NUMERIC(12,4) NOT NULL, missing_components_json JSON NOT NULL,
  input_hash VARCHAR(64) NOT NULL, version VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  CONSTRAINT uq_market_emotion_trade_hash_version UNIQUE (trade_date, input_hash, version)
);

CREATE TABLE IF NOT EXISTS entry_timing_v2_result (
  id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL, trade_date DATE NOT NULL, stock_code VARCHAR(32) NOT NULL,
  stock_name VARCHAR(128), pool_type VARCHAR(32) NOT NULL, selection_source VARCHAR(32), quant_run_id VARCHAR(64) NOT NULL,
  quant_rank INTEGER, quant_score NUMERIC(12,4) NOT NULL, risk_score NUMERIC(12,4), flash_score NUMERIC(12,4),
  strategy_id VARCHAR(32) NOT NULL, strategy_fit_score NUMERIC(12,4) NOT NULL, strategy_confidence NUMERIC(12,4) NOT NULL,
  classification_status VARCHAR(32) NOT NULL, market_emotion_score NUMERIC(12,4), market_emotion_state VARCHAR(32) NOT NULL,
  market_regime VARCHAR(32) NOT NULL, market_gate_status VARCHAR(32) NOT NULL,
  entry_timing_v1_score NUMERIC(12,4) NOT NULL, entry_timing_v2_score NUMERIC(12,4),
  admission_ranking_score_v2 NUMERIC(12,4), admission_status_v1 VARCHAR(32) NOT NULL,
  admission_status_v2 VARCHAR(32) NOT NULL, risk_flags_json JSON NOT NULL, block_reasons_json JSON NOT NULL,
  review_reasons_json JSON NOT NULL, requires_manual_review BOOLEAN NOT NULL,
  component_scores_json JSON NOT NULL, data_coverage_json JSON NOT NULL, diagnostics_json JSON NOT NULL,
  version VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  CONSTRAINT uq_entry_timing_v2_run_stock_pool UNIQUE (run_id, stock_code, pool_type)
);

CREATE INDEX IF NOT EXISTS ix_admission_v2_trade_status ON admission_v2_run (trade_date, status);
CREATE INDEX IF NOT EXISTS ix_entry_timing_v2_trade_status ON entry_timing_v2_result (trade_date, admission_status_v2);
CREATE INDEX IF NOT EXISTS ix_entry_timing_v2_strategy_emotion ON entry_timing_v2_result (strategy_id, market_emotion_state);
