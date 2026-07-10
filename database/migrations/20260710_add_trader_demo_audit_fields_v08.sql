-- Add compact, non-secret failure diagnostics for resumable trader-demo LLM batches.
ALTER TABLE model_validation_llm_audit ADD COLUMN error_category VARCHAR(64);
ALTER TABLE model_validation_llm_audit ADD COLUMN error_field VARCHAR(160);
ALTER TABLE model_validation_llm_audit ADD COLUMN error_message TEXT;
