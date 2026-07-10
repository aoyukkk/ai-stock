-- MODEL_VALIDATION tables are isolated from formal trading and paper-trading tables.
CREATE TABLE IF NOT EXISTS model_validation_run (
    id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL UNIQUE, quant_run_id VARCHAR(64) NOT NULL,
    run_data_manifest_id VARCHAR(64) NOT NULL, run_mode VARCHAR(32) NOT NULL, knowledge_mode VARCHAR(32) NOT NULL,
    decision_time DATETIME NOT NULL, base_market_trade_date DATE NOT NULL, target_trade_date DATE NOT NULL,
    real_llm BOOLEAN NOT NULL DEFAULT 0, status VARCHAR(32) NOT NULL, request_hash VARCHAR(64) NOT NULL UNIQUE,
    config_snapshot JSON NOT NULL, expected_universe_audit JSON NOT NULL, warnings JSON NOT NULL,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS model_validation_sample (
    id INTEGER PRIMARY KEY, validation_run_id VARCHAR(64) NOT NULL, quant_run_id VARCHAR(64) NOT NULL,
    run_data_manifest_id VARCHAR(64) NOT NULL, rank INTEGER NOT NULL, stock_code VARCHAR(32) NOT NULL,
    stock_name VARCHAR(128) NOT NULL, quant_scores JSON NOT NULL, profile_version VARCHAR(64) NOT NULL,
    latest_financial_period VARCHAR(16), financial_available_at DATETIME, data_age_days INTEGER,
    selected_at DATETIME NOT NULL, fundamental_result JSON NOT NULL, screening_result JSON NOT NULL,
    field_provenance JSON NOT NULL, missing_fields JSON NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
    UNIQUE(validation_run_id, rank)
);
CREATE TABLE IF NOT EXISTS model_validation_llm_audit (
    id INTEGER PRIMARY KEY, validation_run_id VARCHAR(64) NOT NULL, stock_code VARCHAR(32) NOT NULL,
    task VARCHAR(64) NOT NULL, knowledge_mode VARCHAR(32) NOT NULL, model_alias VARCHAR(64) NOT NULL,
    actual_model VARCHAR(128), prompt_version VARCHAR(128) NOT NULL, status VARCHAR(32) NOT NULL,
    schema_status VARCHAR(32) NOT NULL, request_hash VARCHAR(64), input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL, cost_usd NUMERIC(18,8), latency_ms INTEGER NOT NULL,
    cache_status VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS model_validation_order_plan (
    id INTEGER PRIMARY KEY, validation_run_id VARCHAR(64) NOT NULL, quant_run_id VARCHAR(64) NOT NULL,
    run_data_manifest_id VARCHAR(64) NOT NULL, stock_code VARCHAR(32) NOT NULL,
    plan_purpose VARCHAR(32) NOT NULL, plan_session VARCHAR(32) NOT NULL, status VARCHAR(32) NOT NULL,
    actionable BOOLEAN NOT NULL DEFAULT 0, is_final_recommendation BOOLEAN NOT NULL DEFAULT 0,
    decision_time DATETIME NOT NULL, base_market_trade_date DATE NOT NULL, target_trade_date DATE NOT NULL,
    factor_version VARCHAR(64), config_snapshot JSON NOT NULL, conservative_price NUMERIC(14,4),
    balanced_price NUMERIC(14,4), aggressive_price NUMERIC(14,4), recommended_price NUMERIC(14,4),
    max_acceptable_price NUMERIC(14,4), stop_loss_price NUMERIC(14,4), take_profit_1_price NUMERIC(14,4),
    take_profit_2_price NUMERIC(14,4), fill_probability NUMERIC(10,6), risk_reward NUMERIC(10,4),
    order_price_score NUMERIC(10,4), support NUMERIC(14,4), resistance NUMERIC(14,4), atr NUMERIC(14,4),
    vwap NUMERIC(14,4), previous_close NUMERIC(14,4), limit_up_estimated NUMERIC(14,4),
    limit_down_estimated NUMERIC(14,4), limit_price_source VARCHAR(32) NOT NULL,
    official_target_day_limit_available BOOLEAN NOT NULL DEFAULT 0, target_day_auction_available BOOLEAN NOT NULL DEFAULT 0,
    cancel_conditions JSON NOT NULL, reprice_conditions JSON NOT NULL, warnings JSON NOT NULL,
    temporal_status VARCHAR(32) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
    UNIQUE(validation_run_id, stock_code)
);
CREATE TABLE IF NOT EXISTS validation_account_snapshot (
    id INTEGER PRIMARY KEY, snapshot_id VARCHAR(64) NOT NULL UNIQUE, validation_run_id VARCHAR(64) NOT NULL,
    account_type VARCHAR(32) NOT NULL, account_equity NUMERIC(20,4) NOT NULL,
    available_cash NUMERIC(20,4) NOT NULL, snapshot_time DATETIME NOT NULL, existing_positions JSON NOT NULL,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS model_validation_allocation (
    id INTEGER PRIMARY KEY, validation_run_id VARCHAR(64) NOT NULL, allocation_run_id VARCHAR(64) NOT NULL,
    account_snapshot_id VARCHAR(64) NOT NULL, stock_code VARCHAR(32) NOT NULL,
    allocation_purpose VARCHAR(32) NOT NULL, actionable BOOLEAN NOT NULL DEFAULT 0, status VARCHAR(32) NOT NULL,
    relative_allocation_weight NUMERIC(12,8) NOT NULL, suggested_position_percent NUMERIC(12,8) NOT NULL,
    suggested_capital_amount NUMERIC(20,4) NOT NULL, suggested_quantity INTEGER NOT NULL,
    estimated_max_loss NUMERIC(20,4) NOT NULL, binding_constraints JSON NOT NULL, warnings JSON NOT NULL,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, UNIQUE(validation_run_id, stock_code)
);
