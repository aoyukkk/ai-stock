-- Add non-sensitive structured response diagnostics and an append-only failure ledger.
ALTER TABLE model_validation_llm_audit ADD COLUMN diagnostics JSON NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS model_validation_failure_audit (
    id INTEGER PRIMARY KEY,
    validation_run_id VARCHAR(64) NOT NULL,
    stock_code VARCHAR(32) NOT NULL,
    task VARCHAR(64) NOT NULL,
    attempt_id VARCHAR(64) NOT NULL,
    prompt_version VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    error_category VARCHAR(64) NOT NULL,
    error_field VARCHAR(160) NOT NULL,
    error_message TEXT NOT NULL,
    diagnostics JSON NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_model_validation_failure_run_stock
    ON model_validation_failure_audit (validation_run_id, stock_code);
