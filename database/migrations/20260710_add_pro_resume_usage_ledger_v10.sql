ALTER TABLE llm_usage ADD COLUMN call_id VARCHAR(128);
ALTER TABLE llm_usage ADD COLUMN pipeline_run_id VARCHAR(128);
ALTER TABLE llm_usage ADD COLUMN validation_run_id VARCHAR(128);
ALTER TABLE llm_usage ADD COLUMN pro_resume_run_id VARCHAR(128);
ALTER TABLE llm_usage ADD COLUMN usage_source VARCHAR(32);
ALTER TABLE llm_usage ADD COLUMN is_cached BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN is_reused BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN http_status INTEGER;
ALTER TABLE llm_usage ADD COLUMN finish_reason VARCHAR(64);
ALTER TABLE llm_usage ADD COLUMN provider_request_id VARCHAR(128);
ALTER TABLE llm_usage ADD COLUMN response_hash VARCHAR(64);
ALTER TABLE llm_usage ADD COLUMN error_category VARCHAR(128);
ALTER TABLE llm_usage ADD COLUMN response_metadata JSON NOT NULL DEFAULT '{}';

CREATE UNIQUE INDEX IF NOT EXISTS ix_llm_usage_call_id ON llm_usage(call_id);
CREATE INDEX IF NOT EXISTS ix_llm_usage_pipeline_run_id ON llm_usage(pipeline_run_id);
CREATE INDEX IF NOT EXISTS ix_llm_usage_validation_run_id ON llm_usage(validation_run_id);
CREATE INDEX IF NOT EXISTS ix_llm_usage_pro_resume_run_id ON llm_usage(pro_resume_run_id);
