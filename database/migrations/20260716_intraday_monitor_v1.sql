CREATE TABLE IF NOT EXISTS intraday_monitor_session (
  id INTEGER PRIMARY KEY, trade_date DATE NOT NULL, status VARCHAR(40) NOT NULL,
  started_at DATETIME, paused_at DATETIME, resumed_at DATETIME, stopped_at DATETIME,
  market_session VARCHAR(32) NOT NULL, pool_version INTEGER NOT NULL DEFAULT 0,
  pool_hash VARCHAR(64) NOT NULL DEFAULT '', stock_count INTEGER NOT NULL DEFAULT 0,
  source_run_ids_json JSON NOT NULL, config_snapshot_json JSON NOT NULL,
  external_call_count INTEGER NOT NULL DEFAULT 0, cache_hit_count INTEGER NOT NULL DEFAULT 0,
  alert_count INTEGER NOT NULL DEFAULT 0, critical_alert_count INTEGER NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_monitor_session_trade_status ON intraday_monitor_session(trade_date, status);

CREATE TABLE IF NOT EXISTS intraday_monitor_pool_version (
  id INTEGER PRIMARY KEY, monitor_session_id INTEGER NOT NULL, version INTEGER NOT NULL,
  source VARCHAR(64) NOT NULL, source_midday_run_id VARCHAR(64), raw_count INTEGER NOT NULL,
  deduplicated_count INTEGER NOT NULL, added_count INTEGER NOT NULL, removed_count INTEGER NOT NULL,
  pool_hash VARCHAR(64) NOT NULL, confirmed_by VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  UNIQUE(monitor_session_id, version)
);

CREATE TABLE IF NOT EXISTS intraday_monitor_item (
  id INTEGER PRIMARY KEY, monitor_session_id INTEGER NOT NULL, pool_version_id INTEGER NOT NULL,
  stock_code VARCHAR(32) NOT NULL, stock_name_snapshot VARCHAR(128), source_json JSON NOT NULL,
  monitor_profile VARCHAR(40) NOT NULL, priority VARCHAR(16) NOT NULL, plan_id INTEGER,
  position_snapshot_id INTEGER, active BOOLEAN NOT NULL, paused BOOLEAN NOT NULL,
  muted_until DATETIME, valid_from DATETIME, valid_until DATETIME,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  UNIQUE(monitor_session_id, stock_code)
);

CREATE TABLE IF NOT EXISTS intraday_monitor_rule (
  id INTEGER PRIMARY KEY, monitor_item_id INTEGER NOT NULL, rule_type VARCHAR(64) NOT NULL,
  threshold_json JSON NOT NULL, comparison VARCHAR(16) NOT NULL, severity VARCHAR(16) NOT NULL,
  cooldown_seconds INTEGER NOT NULL, consecutive_hits_required INTEGER NOT NULL,
  hysteresis_percent FLOAT NOT NULL, enabled BOOLEAN NOT NULL, rule_version VARCHAR(16) NOT NULL,
  source VARCHAR(32) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS intraday_monitor_alert (
  id INTEGER PRIMARY KEY, monitor_session_id INTEGER NOT NULL, monitor_item_id INTEGER NOT NULL,
  rule_id INTEGER NOT NULL, stock_code VARCHAR(32) NOT NULL, triggered_at DATETIME NOT NULL,
  severity VARCHAR(16) NOT NULL, title VARCHAR(128) NOT NULL, message TEXT NOT NULL,
  current_value_json JSON NOT NULL, threshold_json JSON NOT NULL, market_snapshot_id INTEGER,
  minute_snapshot_hash VARCHAR(64), provider_time VARCHAR(64), freshness_status VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL, dedup_key VARCHAR(256) NOT NULL, occurrence_count INTEGER NOT NULL,
  escalated_from_alert_id INTEGER, acknowledged_at DATETIME, acknowledged_by VARCHAR(64), resolution TEXT,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_monitor_alert_session_status ON intraday_monitor_alert(monitor_session_id, status);
CREATE INDEX IF NOT EXISTS ix_monitor_alert_dedup ON intraday_monitor_alert(dedup_key);

CREATE TABLE IF NOT EXISTS intraday_monitor_alert_action (
  id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL, action VARCHAR(32) NOT NULL,
  operated_at DATETIME NOT NULL, operated_by VARCHAR(64) NOT NULL, note TEXT,
  previous_status VARCHAR(32) NOT NULL, new_status VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS intraday_monitor_refresh (
  id INTEGER PRIMARY KEY, monitor_session_id INTEGER NOT NULL, refresh_type VARCHAR(32) NOT NULL,
  priority VARCHAR(8) NOT NULL, started_at DATETIME NOT NULL, completed_at DATETIME,
  requested_codes JSON NOT NULL, returned_codes JSON NOT NULL, missing_codes JSON NOT NULL,
  external_calls INTEGER NOT NULL, cache_hits INTEGER NOT NULL, latency_ms INTEGER,
  status VARCHAR(32) NOT NULL, error_category VARCHAR(64),
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
