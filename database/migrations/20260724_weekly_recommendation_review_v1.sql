CREATE TABLE IF NOT EXISTS weekly_recommendation_review_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(64) NOT NULL,
    review_start_date DATE NOT NULL,
    review_end_date DATE NOT NULL,
    evaluation_date DATE NOT NULL,
    baseline_version VARCHAR(64) NOT NULL,
    status VARCHAR(48) NOT NULL,
    sample_status VARCHAR(48) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    content_hash VARCHAR(64),
    workbook_path VARCHAR(512),
    workbook_hash VARCHAR(64),
    summary JSON NOT NULL DEFAULT '{}',
    audit JSON NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_weekly_recommendation_review_run_id
ON weekly_recommendation_review_run(run_id);

CREATE TABLE IF NOT EXISTS weekly_recommendation_review_detail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(64) NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    recommendation_date DATE NOT NULL,
    target_trade_date DATE,
    stock_code VARCHAR(32) NOT NULL,
    stock_name VARCHAR(128),
    industry VARCHAR(128),
    quant_run_id VARCHAR(64),
    pro_run_id VARCHAR(64),
    quant_rank INTEGER,
    quant_score NUMERIC(12,6),
    pro_rank INTEGER,
    pro_score NUMERIC(12,6),
    recommendation_grade VARCHAR(32),
    entry_status VARCHAR(48) NOT NULL,
    entry_source VARCHAR(64),
    entry_price NUMERIC(16,6),
    max_acceptable_price NUMERIC(16,6),
    stop_loss_price NUMERIC(16,6),
    current_net_return NUMERIC(16,8),
    mfe NUMERIC(16,8),
    mae NUMERIC(16,8),
    giveback NUMERIC(16,8),
    stop_hit BOOLEAN NOT NULL DEFAULT 0,
    risk_path_bad BOOLEAN NOT NULL DEFAULT 0,
    result_class VARCHAR(48) NOT NULL,
    eligible BOOLEAN NOT NULL DEFAULT 0,
    metadata_json JSON NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_weekly_recommendation_review_detail_unique
ON weekly_recommendation_review_detail(run_id, source_type, recommendation_date, stock_code);
CREATE INDEX IF NOT EXISTS ix_weekly_recommendation_review_detail_class
ON weekly_recommendation_review_detail(run_id, result_class);

CREATE TABLE IF NOT EXISTS weekly_recommendation_review_supersession (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    superseded_run_id VARCHAR(64) NOT NULL,
    replacement_run_id VARCHAR(64) NOT NULL,
    reason VARCHAR(256) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(superseded_run_id, replacement_run_id)
);

CREATE TRIGGER IF NOT EXISTS weekly_recommendation_review_run_immutable_update
BEFORE UPDATE ON weekly_recommendation_review_run
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_WEEKLY_REVIEW_RUN'); END;
CREATE TRIGGER IF NOT EXISTS weekly_recommendation_review_run_immutable_delete
BEFORE DELETE ON weekly_recommendation_review_run
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_WEEKLY_REVIEW_RUN'); END;
CREATE TRIGGER IF NOT EXISTS weekly_recommendation_review_detail_immutable_update
BEFORE UPDATE ON weekly_recommendation_review_detail
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_WEEKLY_REVIEW_DETAIL'); END;
CREATE TRIGGER IF NOT EXISTS weekly_recommendation_review_detail_immutable_delete
BEFORE DELETE ON weekly_recommendation_review_detail
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_WEEKLY_REVIEW_DETAIL'); END;
CREATE TRIGGER IF NOT EXISTS weekly_recommendation_review_supersession_immutable_update
BEFORE UPDATE ON weekly_recommendation_review_supersession
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_WEEKLY_REVIEW_SUPERSESSION'); END;
CREATE TRIGGER IF NOT EXISTS weekly_recommendation_review_supersession_immutable_delete
BEFORE DELETE ON weekly_recommendation_review_supersession
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_WEEKLY_REVIEW_SUPERSESSION'); END;
