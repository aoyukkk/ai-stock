-- Research-only migration. Apply only to data/quant_shadow_research.db.
-- Existing S0-S4 rows remain immutable and are never rewritten.
CREATE TABLE IF NOT EXISTS quant_shadow_missingness (
    shadow_run_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    missing_class TEXT NOT NULL,
    missing_reason TEXT,
    missing_subfactors_json TEXT NOT NULL,
    valid_subfactors_json TEXT NOT NULL,
    capital_data_coverage REAL NOT NULL,
    capital_confidence TEXT NOT NULL,
    reweighted INTEGER NOT NULL,
    comparison_eligible INTEGER NOT NULL,
    score_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (shadow_run_id, stock_code)
);

CREATE TABLE IF NOT EXISTS quant_shadow_data_lineage (
    shadow_run_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    amount_raw REAL,
    amount_raw_unit TEXT NOT NULL,
    amount_cny REAL,
    unit_conversion_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (shadow_run_id, stock_code)
);

CREATE TABLE IF NOT EXISTS quant_shadow_threshold_dependency (
    audit_id TEXT NOT NULL,
    dependency_key TEXT NOT NULL,
    module TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_line INTEGER,
    threshold_name TEXT NOT NULL,
    threshold_value REAL,
    score_field TEXT NOT NULL,
    selection_mode TEXT NOT NULL,
    rank_selected INTEGER NOT NULL,
    absolute_score_selected INTEGER NOT NULL,
    global_emotion_shift_can_flip INTEGER NOT NULL,
    s1_flip_count INTEGER,
    s2_flip_count INTEGER,
    s2_1_flip_count INTEGER,
    s3_global_flip_count INTEGER,
    s3_differentiated_flip_count INTEGER,
    s4_flip_count INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (audit_id, dependency_key)
);

CREATE TABLE IF NOT EXISTS quant_shadow_version_registry (
    registry_id TEXT NOT NULL,
    version_key TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    version_tier TEXT NOT NULL,
    frozen_for_forward INTEGER NOT NULL,
    retrospective_trade_date TEXT NOT NULL,
    forward_effective_trade_date TEXT NOT NULL,
    promotion_status TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (registry_id, version_key)
);

CREATE TABLE IF NOT EXISTS quant_shadow_forward_sample (
    sample_id TEXT PRIMARY KEY,
    registry_id TEXT NOT NULL,
    version_key TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    sample_type TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    rank INTEGER NOT NULL,
    in_top20 INTEGER NOT NULL,
    in_top50 INTEGER NOT NULL,
    in_top100 INTEGER NOT NULL,
    technical_score REAL NOT NULL,
    capital_score REAL NOT NULL,
    emotion_score REAL NOT NULL,
    momentum_score REAL NOT NULL,
    risk_score REAL NOT NULL,
    total_score REAL NOT NULL,
    capital_data_coverage REAL NOT NULL,
    capital_confidence TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    universe_snapshot_id TEXT NOT NULL,
    observation_end_ts TEXT,
    available_at_ts TEXT,
    signal_generated_at TEXT,
    order_eligible_at TEXT,
    timing_contract_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (registry_id, version_key, trade_date, stock_code, sample_type)
);

CREATE TABLE IF NOT EXISTS quant_shadow_forward_outcome (
    sample_id TEXT NOT NULL,
    execution_policy TEXT NOT NULL,
    entry_trade_date TEXT,
    entry_price REAL,
    entry_status TEXT NOT NULL,
    return_t1_open REAL,
    return_d1 REAL,
    return_d3 REAL,
    return_d5 REAL,
    mfe REAL,
    mae REAL,
    full_a_equal_weight_excess REAL,
    industry_equal_weight_excess REAL,
    data_status TEXT NOT NULL,
    outcome_input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (sample_id, execution_policy)
);

CREATE TABLE IF NOT EXISTS quant_shadow_workbook_artifact (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    workbook_path TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    style_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS quant_shadow_missingness_immutable_update
BEFORE UPDATE ON quant_shadow_missingness BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_MISSINGNESS'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_missingness_immutable_delete
BEFORE DELETE ON quant_shadow_missingness BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_MISSINGNESS'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_data_lineage_immutable_update
BEFORE UPDATE ON quant_shadow_data_lineage BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_DATA_LINEAGE'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_data_lineage_immutable_delete
BEFORE DELETE ON quant_shadow_data_lineage BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_DATA_LINEAGE'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_threshold_dependency_immutable_update
BEFORE UPDATE ON quant_shadow_threshold_dependency BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_THRESHOLD_AUDIT'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_threshold_dependency_immutable_delete
BEFORE DELETE ON quant_shadow_threshold_dependency BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_THRESHOLD_AUDIT'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_version_registry_immutable_update
BEFORE UPDATE ON quant_shadow_version_registry BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_FORWARD_REGISTRY'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_version_registry_immutable_delete
BEFORE DELETE ON quant_shadow_version_registry BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_FORWARD_REGISTRY'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_forward_sample_immutable_update
BEFORE UPDATE ON quant_shadow_forward_sample BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_FORWARD_SAMPLE'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_forward_sample_immutable_delete
BEFORE DELETE ON quant_shadow_forward_sample BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_FORWARD_SAMPLE'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_forward_outcome_immutable_update
BEFORE UPDATE ON quant_shadow_forward_outcome BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_FORWARD_OUTCOME'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_forward_outcome_immutable_delete
BEFORE DELETE ON quant_shadow_forward_outcome BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_FORWARD_OUTCOME'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_workbook_artifact_immutable_update
BEFORE UPDATE ON quant_shadow_workbook_artifact BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_WORKBOOK_ARTIFACT'); END;
CREATE TRIGGER IF NOT EXISTS quant_shadow_workbook_artifact_immutable_delete
BEFORE DELETE ON quant_shadow_workbook_artifact BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_WORKBOOK_ARTIFACT'); END;
