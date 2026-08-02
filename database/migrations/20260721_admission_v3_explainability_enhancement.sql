ALTER TABLE admission_v3_result ADD COLUMN expected_value_score NUMERIC(14,6);
ALTER TABLE admission_v3_result ADD COLUMN risk_adjusted_opportunity_score NUMERIC(14,6);

CREATE TABLE IF NOT EXISTS factor_performance_history (
    id INTEGER PRIMARY KEY,
    factor_family VARCHAR(32) NOT NULL,
    sample_count INTEGER NOT NULL,
    win_rate NUMERIC(14,6),
    avg_return_d1 NUMERIC(14,6),
    avg_return_d3 NUMERIC(14,6),
    avg_return_d5 NUMERIC(14,6),
    avg_drawdown NUMERIC(14,6),
    positive_contribution_rate NUMERIC(14,6),
    negative_contribution_rate NUMERIC(14,6),
    period VARCHAR(64) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_factor_performance_input_family UNIQUE (input_hash, factor_family)
);

CREATE INDEX IF NOT EXISTS ix_factor_performance_period_family
ON factor_performance_history(period_end, factor_family);
