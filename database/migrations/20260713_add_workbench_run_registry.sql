CREATE TABLE IF NOT EXISTS workbench_run_registry (
    id INTEGER NOT NULL PRIMARY KEY,
    trade_date DATE NOT NULL,
    pipeline_run_id VARCHAR(128) NOT NULL,
    quant_run_id VARCHAR(64) NOT NULL,
    manifest_id VARCHAR(64) NOT NULL,
    flash_run_id VARCHAR(64) NOT NULL,
    pro_run_id VARCHAR(64) NOT NULL,
    candidate_set_hash VARCHAR(64) NOT NULL,
    order_run_id VARCHAR(128),
    position_run_id VARCHAR(128),
    export_path TEXT,
    export_sha256 VARCHAR(64),
    final_status VARCHAR(32) NOT NULL,
    source VARCHAR(32) NOT NULL,
    counts JSON NOT NULL,
    validation JSON NOT NULL,
    reconciled_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_workbench_registry_pipeline_candidate
        UNIQUE (trade_date, pipeline_run_id, candidate_set_hash)
);

CREATE INDEX IF NOT EXISTS ix_workbench_registry_trade_date
    ON workbench_run_registry (trade_date, reconciled_at);
