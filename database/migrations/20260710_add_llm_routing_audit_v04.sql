-- V0.4 task-tier/model-routing audit fields. Contains no credentials or prompts.
ALTER TABLE llm_usage ADD COLUMN model_alias VARCHAR(128);
ALTER TABLE llm_usage ADD COLUMN task_type VARCHAR(128);
ALTER TABLE llm_usage ADD COLUMN task_tier VARCHAR(32);
ALTER TABLE llm_usage ADD COLUMN thinking_mode VARCHAR(32);
ALTER TABLE llm_usage ADD COLUMN reasoning_effort VARCHAR(32);
ALTER TABLE llm_usage ADD COLUMN input_cache_hit_tokens INTEGER DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN input_cache_miss_tokens INTEGER DEFAULT 0;
ALTER TABLE llm_usage ADD COLUMN cost_status VARCHAR(64);
ALTER TABLE llm_usage ADD COLUMN pricing_version VARCHAR(128);

ALTER TABLE ai_analysis_result ADD COLUMN model_alias VARCHAR(128);
ALTER TABLE ai_analysis_result ADD COLUMN task_tier VARCHAR(32);
ALTER TABLE ai_analysis_result ADD COLUMN request_hash VARCHAR(128);
ALTER TABLE ai_analysis_result ADD COLUMN is_real BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE ai_analysis_result ADD COLUMN structured_result_json JSON;
