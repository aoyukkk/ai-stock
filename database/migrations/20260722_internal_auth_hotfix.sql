-- SQLite-compatible forward migration for the Internal Web authentication hotfix.
-- Apply once to an existing internal-web database before restarting the service.
ALTER TABLE internal_password_credential ADD COLUMN password_initialized BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE internal_password_credential ADD COLUMN password_changed_at DATETIME;
ALTER TABLE internal_password_credential ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE internal_user ADD COLUMN user_key VARCHAR(96);
CREATE UNIQUE INDEX IF NOT EXISTS ix_internal_user_user_key ON internal_user(user_key);
UPDATE internal_password_credential
SET must_change_password = 0
WHERE must_change_password = 1;
