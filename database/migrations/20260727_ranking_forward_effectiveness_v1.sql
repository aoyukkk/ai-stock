-- Additive, shadow-only ranking effectiveness evaluation.
-- Does not alter Quant, LLM, order, position, or decision snapshot tables.

CREATE TABLE IF NOT EXISTS ranking_evaluation_state (
  evaluation_version VARCHAR(64) NOT NULL UNIQUE,
  activation_date DATE NOT NULL,
  activated_by_snapshot_id VARCHAR(64) NOT NULL,
  configuration_hash VARCHAR(64) NOT NULL,
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS ranking_evaluation_snapshot (
  evaluation_version VARCHAR(64) NOT NULL,
  snapshot_id VARCHAR(64) NOT NULL UNIQUE,
  snapshot_run_id VARCHAR(64) NOT NULL,
  ranking_trade_date DATE NOT NULL,
  generated_at DATETIME NOT NULL,
  source_quant_run_id VARCHAR(128) NOT NULL,
  model_name VARCHAR(96) NOT NULL,
  score_version VARCHAR(96) NOT NULL,
  factor_version VARCHAR(96) NOT NULL,
  production_or_shadow VARCHAR(16) NOT NULL,
  price_basis VARCHAR(32) NOT NULL,
  return_basis VARCHAR(32) NOT NULL,
  top_n INTEGER NOT NULL,
  source_input_hash VARCHAR(64) NOT NULL,
  snapshot_hash VARCHAR(64) NOT NULL,
  evaluation_scope VARCHAR(64) NOT NULL,
  snapshot_origin VARCHAR(32) NOT NULL,
  overall_data_status VARCHAR(16) NOT NULL,
  raw_artifacts_json JSON NOT NULL,
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  CONSTRAINT uq_ranking_eval_date_factor_scope UNIQUE
    (ranking_trade_date, factor_version, evaluation_scope)
);
CREATE INDEX IF NOT EXISTS ix_ranking_eval_snapshot_source
  ON ranking_evaluation_snapshot(source_quant_run_id);
CREATE INDEX IF NOT EXISTS ix_ranking_eval_snapshot_date_version
  ON ranking_evaluation_snapshot(ranking_trade_date, factor_version);

CREATE TABLE IF NOT EXISTS ranking_evaluation_snapshot_item (
  snapshot_id INTEGER NOT NULL REFERENCES ranking_evaluation_snapshot(id),
  stock_code VARCHAR(32) NOT NULL,
  ts_code VARCHAR(32) NOT NULL,
  stock_name VARCHAR(128) NOT NULL,
  original_rank INTEGER NOT NULL,
  quant_score NUMERIC(20,10),
  baseline_trade_date DATE NOT NULL,
  baseline_close NUMERIC(18,6),
  baseline_price_source VARCHAR(64) NOT NULL,
  baseline_source_hash VARCHAR(64),
  original_group VARCHAR(16) NOT NULL,
  source_row_number INTEGER NOT NULL,
  row_data_status VARCHAR(16) NOT NULL,
  row_issue_code VARCHAR(64),
  row_issue_detail TEXT,
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  CONSTRAINT uq_ranking_eval_snapshot_row UNIQUE(snapshot_id, source_row_number)
);
CREATE INDEX IF NOT EXISTS ix_ranking_eval_snapshot_rank
  ON ranking_evaluation_snapshot_item(snapshot_id, original_rank);
CREATE INDEX IF NOT EXISTS ix_ranking_eval_snapshot_stock
  ON ranking_evaluation_snapshot_item(snapshot_id, stock_code);

CREATE TABLE IF NOT EXISTS ranking_evaluation_forward_outcome (
  snapshot_item_id INTEGER NOT NULL REFERENCES ranking_evaluation_snapshot_item(id),
  horizon INTEGER NOT NULL,
  due_trade_date DATE NOT NULL,
  baseline_close NUMERIC(18,6),
  future_close NUMERIC(18,6),
  return_decimal NUMERIC(20,10),
  return_percent NUMERIC(20,10),
  adjusted_return_decimal NUMERIC(20,10),
  adjusted_return_percent NUMERIC(20,10),
  baseline_adjustment_factor NUMERIC(20,10),
  future_adjustment_factor NUMERIC(20,10),
  baseline_price_source VARCHAR(64) NOT NULL,
  future_price_source VARCHAR(64),
  source_data_hash VARCHAR(64),
  outcome_status VARCHAR(40) NOT NULL,
  missing_reason TEXT,
  corporate_action_flag VARCHAR(32) NOT NULL,
  calculated_at DATETIME,
  data_as_of_time DATETIME,
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  CONSTRAINT uq_ranking_eval_item_horizon UNIQUE(snapshot_item_id, horizon)
);
CREATE INDEX IF NOT EXISTS ix_ranking_eval_outcome_due_status
  ON ranking_evaluation_forward_outcome(due_trade_date, outcome_status);

CREATE TABLE IF NOT EXISTS ranking_evaluation_daily_metric (
  snapshot_id INTEGER NOT NULL REFERENCES ranking_evaluation_snapshot(id),
  evaluation_version VARCHAR(64) NOT NULL,
  factor_version VARCHAR(96) NOT NULL,
  ranking_trade_date DATE NOT NULL,
  horizon INTEGER NOT NULL,
  return_basis VARCHAR(32) NOT NULL,
  valid_sample_count INTEGER NOT NULL,
  missing_sample_count INTEGER NOT NULL,
  coverage_ratio NUMERIC(20,10) NOT NULL,
  rank_ic NUMERIC(20,10),
  calculation_status VARCHAR(32) NOT NULL,
  top20_mean_return NUMERIC(20,10),
  bottom20_mean_return NUMERIC(20,10),
  spread NUMERIC(20,10),
  top20_valid_count INTEGER NOT NULL,
  bottom20_valid_count INTEGER NOT NULL,
  top20_coverage_ratio NUMERIC(20,10) NOT NULL,
  bottom20_coverage_ratio NUMERIC(20,10) NOT NULL,
  group_returns_json JSON NOT NULL,
  group_valid_counts_json JSON NOT NULL,
  group_coverage_json JSON NOT NULL,
  adjacent_spreads_json JSON NOT NULL,
  monotonicity_pass_count INTEGER,
  monotonicity_label VARCHAR(32) NOT NULL,
  metric_input_hash VARCHAR(64) NOT NULL,
  calculated_at DATETIME NOT NULL,
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  CONSTRAINT uq_ranking_eval_daily_metric UNIQUE(snapshot_id, horizon, return_basis)
);
CREATE INDEX IF NOT EXISTS ix_ranking_eval_metric_factor_date
  ON ranking_evaluation_daily_metric(factor_version, ranking_trade_date);

CREATE TABLE IF NOT EXISTS ranking_evaluation_weekly_run (
  run_id VARCHAR(64) NOT NULL UNIQUE,
  factor_version VARCHAR(96) NOT NULL,
  week_ending DATE NOT NULL,
  evaluation_version VARCHAR(64) NOT NULL,
  activation_date DATE NOT NULL,
  as_of_trade_date DATE,
  return_basis VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  data_status VARCHAR(16) NOT NULL,
  summary_json JSON NOT NULL,
  input_hash VARCHAR(64) NOT NULL,
  report_hash VARCHAR(64) NOT NULL,
  artifact_paths_json JSON NOT NULL,
  generated_at DATETIME NOT NULL,
  completed_at DATETIME,
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  CONSTRAINT uq_ranking_eval_week_version UNIQUE
    (factor_version, week_ending, evaluation_version)
);
CREATE INDEX IF NOT EXISTS ix_ranking_eval_week_status
  ON ranking_evaluation_weekly_run(week_ending, status);

CREATE TABLE IF NOT EXISTS ranking_evaluation_data_issue (
  issue_id VARCHAR(64) NOT NULL UNIQUE,
  issue_hash VARCHAR(64) NOT NULL UNIQUE,
  snapshot_id INTEGER REFERENCES ranking_evaluation_snapshot(id),
  weekly_run_id INTEGER REFERENCES ranking_evaluation_weekly_run(id),
  issue_code VARCHAR(64) NOT NULL,
  issue_level VARCHAR(16) NOT NULL,
  affected_date DATE,
  affected_stock VARCHAR(32),
  affected_version VARCHAR(96),
  detail TEXT NOT NULL,
  detected_at DATETIME NOT NULL,
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ranking_eval_issue_version_date
  ON ranking_evaluation_data_issue(affected_version, affected_date);

CREATE TABLE IF NOT EXISTS ranking_evaluation_artifact (
  artifact_id VARCHAR(64) NOT NULL UNIQUE,
  weekly_run_id INTEGER NOT NULL REFERENCES ranking_evaluation_weekly_run(id),
  artifact_type VARCHAR(32) NOT NULL,
  artifact_path TEXT NOT NULL UNIQUE,
  input_hash VARCHAR(64) NOT NULL,
  content_hash VARCHAR(64) NOT NULL,
  style_hash VARCHAR(64),
  immutable BOOLEAN NOT NULL DEFAULT 1,
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ranking_eval_artifact_run
  ON ranking_evaluation_artifact(weekly_run_id);

CREATE TRIGGER IF NOT EXISTS ranking_eval_state_immutable_update
BEFORE UPDATE ON ranking_evaluation_state BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_STATE');
END;
CREATE TRIGGER IF NOT EXISTS ranking_eval_state_immutable_delete
BEFORE DELETE ON ranking_evaluation_state BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_STATE');
END;
CREATE TRIGGER IF NOT EXISTS ranking_eval_snapshot_immutable_update
BEFORE UPDATE ON ranking_evaluation_snapshot BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_SNAPSHOT');
END;
CREATE TRIGGER IF NOT EXISTS ranking_eval_snapshot_immutable_delete
BEFORE DELETE ON ranking_evaluation_snapshot BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_SNAPSHOT');
END;
CREATE TRIGGER IF NOT EXISTS ranking_eval_item_immutable_update
BEFORE UPDATE ON ranking_evaluation_snapshot_item BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_ITEM');
END;
CREATE TRIGGER IF NOT EXISTS ranking_eval_item_immutable_delete
BEFORE DELETE ON ranking_evaluation_snapshot_item BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_ITEM');
END;
CREATE TRIGGER IF NOT EXISTS ranking_eval_week_immutable_update
BEFORE UPDATE ON ranking_evaluation_weekly_run BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_WEEK');
END;
CREATE TRIGGER IF NOT EXISTS ranking_eval_week_immutable_delete
BEFORE DELETE ON ranking_evaluation_weekly_run BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_WEEK');
END;
CREATE TRIGGER IF NOT EXISTS ranking_eval_artifact_immutable_update
BEFORE UPDATE ON ranking_evaluation_artifact BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_ARTIFACT');
END;
CREATE TRIGGER IF NOT EXISTS ranking_eval_artifact_immutable_delete
BEFORE DELETE ON ranking_evaluation_artifact BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_RANKING_EVALUATION_ARTIFACT');
END;
