CREATE TABLE IF NOT EXISTS event_evidence_snapshot (
  id INTEGER PRIMARY KEY, snapshot_id VARCHAR(64) NOT NULL UNIQUE,
  run_id VARCHAR(64) NOT NULL, trade_date DATE NOT NULL,
  decision_as_of_time DATETIME NOT NULL, stock_code VARCHAR(32) NOT NULL,
  query TEXT NOT NULL, search_status VARCHAR(64) NOT NULL,
  provider VARCHAR(64) NOT NULL, provider_verified BOOLEAN NOT NULL,
  direct_search_used BOOLEAN NOT NULL, confidence_discount_applied BOOLEAN NOT NULL,
  production_eligible BOOLEAN NOT NULL, shadow_eligible BOOLEAN NOT NULL,
  input_hash VARCHAR(64) NOT NULL, content_hash VARCHAR(64) NOT NULL,
  contract_version VARCHAR(64) NOT NULL, created_at DATETIME, updated_at DATETIME
);
CREATE INDEX IF NOT EXISTS ix_event_snapshot_run_stock ON event_evidence_snapshot(run_id, stock_code);

CREATE TABLE IF NOT EXISTS event_evidence_item (
  id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL REFERENCES event_evidence_snapshot(id),
  event_id VARCHAR(64) NOT NULL, event_cluster_id VARCHAR(64) NOT NULL,
  event_type VARCHAR(64) NOT NULL, title VARCHAR(500) NOT NULL, summary TEXT NOT NULL,
  url TEXT, canonical_url TEXT, domain VARCHAR(255), published_at DATETIME,
  retrieved_at DATETIME NOT NULL, source_tier VARCHAR(16) NOT NULL,
  source_type VARCHAR(64) NOT NULL, provider VARCHAR(64) NOT NULL,
  provider_verified BOOLEAN NOT NULL, event_direction VARCHAR(16) NOT NULL,
  materiality FLOAT NOT NULL, relevance FLOAT NOT NULL, confidence FLOAT NOT NULL,
  temporal_status VARCHAR(64) NOT NULL, content_hash VARCHAR(64) NOT NULL,
  revision INTEGER NOT NULL, raw_metadata_json JSON NOT NULL,
  created_at DATETIME, updated_at DATETIME,
  UNIQUE(snapshot_id, event_id)
);
CREATE INDEX IF NOT EXISTS ix_event_item_cluster ON event_evidence_item(event_cluster_id);

CREATE TABLE IF NOT EXISTS event_review_result (
  id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL,
  snapshot_id INTEGER NOT NULL REFERENCES event_evidence_snapshot(id),
  stock_code VARCHAR(32) NOT NULL, search_status VARCHAR(64) NOT NULL,
  event_opportunity_score FLOAT NOT NULL, evidence_confidence FLOAT NOT NULL,
  breadth_score FLOAT NOT NULL, risk_action VARCHAR(32) NOT NULL,
  conflicts_json JSON NOT NULL, warnings_json JSON NOT NULL,
  review_version VARCHAR(64) NOT NULL, created_at DATETIME, updated_at DATETIME,
  UNIQUE(run_id, stock_code)
);

CREATE TABLE IF NOT EXISTS event_screening_run (
  id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL UNIQUE, trade_date DATE NOT NULL,
  decision_as_of_time DATETIME NOT NULL, source_run_id VARCHAR(128) NOT NULL,
  source_input_hash VARCHAR(64) NOT NULL, universe_snapshot_id VARCHAR(64) NOT NULL,
  factor_version VARCHAR(96) NOT NULL, screening_version VARCHAR(96) NOT NULL,
  decision_version VARCHAR(96) NOT NULL, production_or_shadow VARCHAR(16) NOT NULL,
  execution_mode VARCHAR(32) NOT NULL, real_search_enabled BOOLEAN NOT NULL,
  historical_replay BOOLEAN NOT NULL, input_count INTEGER NOT NULL, output_count INTEGER NOT NULL,
  actual_network_calls INTEGER NOT NULL, logical_evaluations INTEGER NOT NULL,
  reused_checkpoint_count INTEGER NOT NULL, stale_checkpoint_count INTEGER NOT NULL,
  content_hash VARCHAR(64) NOT NULL, manifest_json JSON NOT NULL,
  created_at DATETIME, updated_at DATETIME
);
CREATE INDEX IF NOT EXISTS ix_event_screening_date_version ON event_screening_run(trade_date, screening_version);

CREATE TABLE IF NOT EXISTS event_screening_item (
  id INTEGER PRIMARY KEY, screening_run_id INTEGER NOT NULL REFERENCES event_screening_run(id),
  stock_code VARCHAR(32) NOT NULL, stock_name VARCHAR(128) NOT NULL,
  quant_rank INTEGER NOT NULL, quant_score FLOAT NOT NULL,
  event_opportunity_score FLOAT NOT NULL, evidence_confidence FLOAT NOT NULL,
  evidence_breadth FLOAT NOT NULL, risk_action VARCHAR(32) NOT NULL,
  v3_screening_score FLOAT NOT NULL, v3_rank INTEGER NOT NULL,
  selected_top20 BOOLEAN NOT NULL, search_status VARCHAR(64) NOT NULL,
  event_snapshot_id VARCHAR(64) NOT NULL, checkpoint_status VARCHAR(32) NOT NULL,
  hard_gate_reasons_json JSON NOT NULL, raw_quant_json JSON NOT NULL,
  created_at DATETIME, updated_at DATETIME, UNIQUE(screening_run_id, stock_code)
);
CREATE INDEX IF NOT EXISTS ix_event_screening_rank ON event_screening_item(screening_run_id, v3_rank);

CREATE TABLE IF NOT EXISTS event_overlay_data_issue (
  id INTEGER PRIMARY KEY, issue_id VARCHAR(64) NOT NULL UNIQUE,
  issue_hash VARCHAR(64) NOT NULL UNIQUE, run_id VARCHAR(64) NOT NULL,
  stock_code VARCHAR(32), issue_code VARCHAR(64) NOT NULL,
  issue_level VARCHAR(16) NOT NULL, detail TEXT NOT NULL,
  detected_at DATETIME NOT NULL, created_at DATETIME, updated_at DATETIME
);
