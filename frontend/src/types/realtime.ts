export interface RealtimeProviderStatus {
  provider: string;
  transport: string;
  integration_mode: string;
  enabled: boolean;
  configured: boolean;
  status: string;
  observed_session_status: string;
  fallback_provider: string;
  scheduler_enabled: boolean;
  real_trading_enabled: boolean;
  usage_count: number;
  last_call_at: string | null;
  quota_remaining: number | null;
}

export interface MonitorPoolItem {
  stock_code: string;
  origin: string;
  origins: string[];
}

export interface RealtimeRow {
  stock_code: string;
  snapshot_time: string;
  provider_time: string | null;
  latest: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount: number | null;
  data_status: string;
  provider: string;
  purpose: string;
}

export interface IndexRow {
  index_code: string;
  trade_date: string;
  close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount: number | null;
  data_status: string;
}

export interface RefreshResult {
  job_id: string;
  pool_count: number;
  batch_count: number;
  cache_status: string;
  status: string;
  items: RealtimeRow[];
  index?: Record<string, unknown>;
}

export interface AcceptanceRunSummary {
  acceptance_run_id: string;
  acceptance_mode: string;
  started_at: string;
  completed_at: string | null;
  trade_date: string;
  market_session: string;
  status: string;
  integration_mode: string;
  index_requested: number;
  index_returned: number;
  stock_requested: number;
  stock_returned: number;
  minute_stock_count: number;
  external_call_count: number;
  cache_hit_count: number;
  database_insert_count: number;
  duplicate_count: number;
  index_coverage_ratio: number | null;
  stock_coverage_ratio: number | null;
  minute_completeness_ratio: number | null;
  realtime_delay_p50: number | null;
  realtime_delay_p95: number | null;
  maximum_delay: number | null;
  provider_timestamp_ratio: number | null;
  dual_source_match_count: number;
  material_conflict_count: number;
  business_immutability_passed: boolean;
  report_path: string | null;
}

export interface MonitorSession {
  id: number;
  trade_date: string;
  status: string;
  market_session: string;
  pool_version: number;
  pool_hash: string;
  stock_count: number;
  external_call_count: number;
  cache_hit_count: number;
  alert_count: number;
  critical_alert_count: number;
  updated_at: string;
}

export interface SelectedMonitorItem {
  id: number;
  monitor_session_id: number;
  stock_code: string;
  stock_name_snapshot: string | null;
  source_json: string[];
  monitor_profile: string;
  priority: string;
  active: boolean;
  paused: boolean;
  latest?: number | null;
  change_percent?: number | null;
  provider_time?: string | null;
  data_status?: string | null;
  current_alert_severity?: string | null;
  current_alert?: string | null;
  last_alert_time?: string | null;
  alert_status?: string | null;
}

export interface MonitorAlert {
  id: number;
  stock_code: string;
  triggered_at: string;
  severity: string;
  title: string;
  message: string;
  current_value_json: Record<string, unknown>;
  threshold_json: Record<string, unknown>;
  provider_time: string | null;
  freshness_status: string;
  status: string;
  occurrence_count: number;
}

export interface MonitorPoolCandidate {
  stock_code: string;
  stock_name?: string | null;
  source?: string;
  sources?: string[];
  monitor_profile: string;
  priority: string;
  recommended_price?: number | null;
  max_acceptable_price?: number | null;
  stop_loss?: number | null;
  take_profit_1?: number | null;
  take_profit_2?: number | null;
  plan_id?: number | null;
  position_snapshot_id?: number | null;
}
