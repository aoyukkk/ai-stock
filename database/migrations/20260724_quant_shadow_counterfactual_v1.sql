-- Applied only to data/quant_shadow_research.db by the research runner.
-- It must never be applied implicitly to the production ai_trader_dev.db.
CREATE TABLE IF NOT EXISTS quant_shadow_run (
    shadow_run_id TEXT PRIMARY KEY,
    base_run_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    universe_snapshot_id TEXT NOT NULL,
    normalization_scope TEXT NOT NULL,
    universe_mode TEXT NOT NULL,
    universe_count INTEGER NOT NULL,
    immutable_payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quant_shadow_score (
    shadow_run_id TEXT NOT NULL,
    base_run_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    universe_snapshot_id TEXT NOT NULL,
    normalization_scope TEXT NOT NULL,
    technical_score REAL NOT NULL,
    capital_score REAL NOT NULL,
    emotion_score REAL NOT NULL,
    momentum_score REAL NOT NULL,
    risk_score REAL NOT NULL,
    total_score REAL NOT NULL,
    rank INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (shadow_run_id, stock_code),
    FOREIGN KEY (shadow_run_id) REFERENCES quant_shadow_run(shadow_run_id)
);

CREATE TABLE IF NOT EXISTS quant_shadow_factor_detail (
    shadow_run_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    factor_group TEXT NOT NULL,
    factor_name TEXT NOT NULL,
    raw_value REAL,
    normalized_value REAL,
    score REAL NOT NULL,
    weight REAL NOT NULL,
    contribution REAL NOT NULL,
    missing_reason TEXT,
    fallback_type TEXT,
    score_origin TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    universe_snapshot_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (shadow_run_id, stock_code, factor_group, factor_name),
    FOREIGN KEY (shadow_run_id) REFERENCES quant_shadow_run(shadow_run_id)
);

CREATE TABLE IF NOT EXISTS quant_shadow_universe_audit (
    shadow_run_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    exclusion_stage TEXT,
    exclusion_reason TEXT,
    raw_amount REAL,
    normalized_amount REAL,
    threshold REAL,
    unit_fix_changes_result INTEGER NOT NULL,
    final_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (shadow_run_id, stock_code)
);

CREATE TABLE IF NOT EXISTS quant_shadow_comparison (
    shadow_run_id TEXT PRIMARY KEY,
    base_run_id TEXT NOT NULL,
    spearman REAL,
    kendall REAL,
    mean_rank_delta REAL,
    median_rank_delta REAL,
    max_rank_delta INTEGER,
    top20_overlap REAL,
    top50_overlap REAL,
    top100_overlap REAL,
    top100_flip_count INTEGER,
    sector_concentration_delta REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quant_data_quality_audit (
    shadow_run_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    factor_group TEXT NOT NULL,
    factor_name TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    issue_detail TEXT,
    confidence_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS quant_shadow_run_immutable_update
BEFORE UPDATE ON quant_shadow_run BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_RUN');
END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_run_immutable_delete
BEFORE DELETE ON quant_shadow_run BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_RUN');
END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_score_immutable_update
BEFORE UPDATE ON quant_shadow_score BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_SCORE');
END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_score_immutable_delete
BEFORE DELETE ON quant_shadow_score BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_SCORE');
END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_factor_detail_immutable_update
BEFORE UPDATE ON quant_shadow_factor_detail BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_FACTOR_DETAIL');
END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_factor_detail_immutable_delete
BEFORE DELETE ON quant_shadow_factor_detail BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_FACTOR_DETAIL');
END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_universe_audit_immutable_update
BEFORE UPDATE ON quant_shadow_universe_audit BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_UNIVERSE_AUDIT');
END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_universe_audit_immutable_delete
BEFORE DELETE ON quant_shadow_universe_audit BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_UNIVERSE_AUDIT');
END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_comparison_immutable_update
BEFORE UPDATE ON quant_shadow_comparison BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_COMPARISON');
END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_comparison_immutable_delete
BEFORE DELETE ON quant_shadow_comparison BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_COMPARISON');
END;
CREATE TRIGGER IF NOT EXISTS quant_data_quality_audit_immutable_update
BEFORE UPDATE ON quant_data_quality_audit BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_QUANT_DATA_QUALITY_AUDIT');
END;
CREATE TRIGGER IF NOT EXISTS quant_data_quality_audit_immutable_delete
BEFORE DELETE ON quant_data_quality_audit BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE_QUANT_DATA_QUALITY_AUDIT');
END;
