CREATE TABLE selection_performance_run (
    id INTEGER PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL UNIQUE,
    performance_input_hash VARCHAR(64) NOT NULL,
    evaluation_end_date DATE NOT NULL,
    lookback_value INTEGER NOT NULL,
    lookback_unit VARCHAR(32) NOT NULL,
    start_selection_date DATE,
    end_selection_date DATE,
    return_basis VARCHAR(32) NOT NULL,
    selection_scope VARCHAR(64) NOT NULL,
    weighting_mode VARCHAR(64) NOT NULL,
    include_zero_position_stocks BOOLEAN NOT NULL,
    include_risk_blocked_stocks BOOLEAN NOT NULL,
    status VARCHAR(32) NOT NULL,
    cohort_count INTEGER NOT NULL DEFAULT 0,
    stock_count INTEGER NOT NULL DEFAULT 0,
    daily_record_count INTEGER NOT NULL DEFAULT 0,
    portfolio_record_count INTEGER NOT NULL DEFAULT 0,
    coverage_ratio NUMERIC(18,10) NOT NULL DEFAULT 0,
    algorithm_version VARCHAR(32) NOT NULL,
    schema_version VARCHAR(32) NOT NULL,
    market_data_watermark_hash VARCHAR(64) NOT NULL,
    trade_calendar_version VARCHAR(64) NOT NULL,
    config_snapshot_json JSON NOT NULL,
    cache_checksum VARCHAR(64),
    cache_row_count INTEGER,
    invalidation_reason TEXT,
    error_message TEXT,
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX ix_selection_performance_hash_status ON selection_performance_run(performance_input_hash, status);

CREATE TABLE selection_cohort (
    id INTEGER PRIMARY KEY,
    selection_trade_date DATE NOT NULL,
    pipeline_run_id VARCHAR(64) NOT NULL UNIQUE,
    quant_run_id VARCHAR(64), flash_run_id VARCHAR(64), pro_run_id VARCHAR(64), position_run_id VARCHAR(64),
    candidate_set_hash VARCHAR(64) NOT NULL,
    stock_count INTEGER NOT NULL, llm_count INTEGER NOT NULL DEFAULT 0,
    manual_count INTEGER NOT NULL DEFAULT 0, both_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL, completed_at TIMESTAMP,
    config_snapshot_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE INDEX ix_selection_cohort_date ON selection_cohort(selection_trade_date);

CREATE TABLE selection_cohort_member (
    id INTEGER PRIMARY KEY,
    cohort_id INTEGER NOT NULL REFERENCES selection_cohort(id),
    stock_code VARCHAR(32) NOT NULL, stock_name_snapshot VARCHAR(128) NOT NULL,
    selection_source VARCHAR(16) NOT NULL,
    quant_rank INTEGER, quant_score NUMERIC(12,6), flash_rank INTEGER, flash_score NUMERIC(12,6),
    flash_decision VARCHAR(64), pro_rank INTEGER, pro_score NUMERIC(12,6),
    suggested_position_percent NUMERIC(18,10), risk_status VARCHAR(64),
    baseline_trade_date DATE, baseline_price NUMERIC(16,6),
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_selection_cohort_member_stock UNIQUE(cohort_id, stock_code)
);

CREATE TABLE selection_performance_daily (
    id INTEGER PRIMARY KEY,
    performance_run_id INTEGER NOT NULL REFERENCES selection_performance_run(id),
    cohort_member_id INTEGER NOT NULL REFERENCES selection_cohort_member(id),
    evaluation_trade_date DATE NOT NULL, holding_day INTEGER NOT NULL,
    baseline_trade_date DATE, baseline_price NUMERIC(16,6),
    open_price NUMERIC(16,6), high_price NUMERIC(16,6), low_price NUMERIC(16,6), close_price NUMERIC(16,6), previous_close NUMERIC(16,6),
    daily_return NUMERIC(18,10), cumulative_return NUMERIC(18,10), peak_cumulative_return NUMERIC(18,10),
    drawdown_to_date NUMERIC(18,10), max_drawdown_to_date NUMERIC(18,10),
    return_source VARCHAR(32) NOT NULL, data_status VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_selection_performance_member_date UNIQUE(performance_run_id, cohort_member_id, evaluation_trade_date)
);

CREATE TABLE selection_portfolio_daily (
    id INTEGER PRIMARY KEY,
    performance_run_id INTEGER NOT NULL REFERENCES selection_performance_run(id),
    cohort_id INTEGER NOT NULL REFERENCES selection_cohort(id),
    evaluation_trade_date DATE NOT NULL, holding_day INTEGER NOT NULL,
    weighting_mode VARCHAR(64) NOT NULL,
    total_member_count INTEGER NOT NULL, valid_member_count INTEGER NOT NULL,
    suspended_count INTEGER NOT NULL, missing_count INTEGER NOT NULL,
    daily_return NUMERIC(18,10), cumulative_return NUMERIC(18,10), win_rate NUMERIC(18,10),
    drawdown_to_date NUMERIC(18,10), max_drawdown_to_date NUMERIC(18,10), coverage_ratio NUMERIC(18,10) NOT NULL,
    best_stock_code VARCHAR(32), worst_stock_code VARCHAR(32), status VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_selection_portfolio_run_date_mode UNIQUE(performance_run_id, cohort_id, evaluation_trade_date, weighting_mode)
);
