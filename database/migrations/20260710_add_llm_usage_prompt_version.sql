-- Adds prompt-version auditability for existing V0.3 databases.
-- No credentials or request/response content are stored by this migration.
ALTER TABLE llm_usage ADD COLUMN prompt_version VARCHAR(64);
