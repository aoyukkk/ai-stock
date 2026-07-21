CREATE TABLE IF NOT EXISTS internal_user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email VARCHAR(320) NOT NULL UNIQUE,
    display_name VARCHAR(128) NOT NULL,
    role VARCHAR(16) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT 1,
    last_login_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS internal_password_credential (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_user_id INTEGER NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS internal_auth_session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_user_id INTEGER NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    csrf_hash VARCHAR(64) NOT NULL,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS internal_audit_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    access_email VARCHAR(320) NOT NULL,
    internal_user_id INTEGER NOT NULL,
    role VARCHAR(16) NOT NULL,
    source_ip VARCHAR(64),
    user_agent_summary VARCHAR(256),
    operation VARCHAR(128) NOT NULL,
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(128),
    job_id VARCHAR(64),
    metadata_json JSON NOT NULL,
    created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS job_execution_lock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lock_key VARCHAR(160) NOT NULL UNIQUE,
    job_id VARCHAR(64) NOT NULL,
    started_by VARCHAR(320) NOT NULL,
    current_stage VARCHAR(64) NOT NULL,
    acquired_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL
);
