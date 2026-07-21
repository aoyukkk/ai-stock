export interface PostCloseStatus {
  run_id?: string;
  trade_date?: string;
  target_trade_date?: string;
  run_mode?: string;
  status: string;
  fast_rule_status?: string;
  pro_review_status?: string;
  stock_count?: number;
  held_count?: number;
  non_held_count?: number;
  rule_duration_ms?: number;
  scoring_profile?: string;
  ifind_mode?: string;
  manual_review_count?: number;
  action_distribution?: Record<string, number>;
  advisory_only?: boolean;
  pool?: Record<string, unknown>;
  tiered_coverage?: Record<string, unknown>;
}

export interface PostCloseActionRow {
  id: number;
  run_id: string;
  stock_code: string;
  stock_name: string | null;
  selection_source: string;
  position_status: string;
  account_scope: string | null;
  quantity: number | null;
  available_quantity: number | null;
  target_day_sellable_quantity: number | null;
  cost_price: number | null;
  close_price: number | null;
  unrealized_return: number | null;
  holding_days: number | null;
  base_score: number | null;
  ifind_shadow_score: number | null;
  enhanced_shadow_score: number | null;
  base_rank: number | null;
  enhanced_rank: number | null;
  action_health_score: number | null;
  baseline_rule_action: string;
  ifind_shadow_action: string;
  pro_review_action: string | null;
  current_adopted_action: string;
  current_position_percent: number | null;
  suggested_target_position_percent: number | null;
  suggested_reduce_percent: number | null;
  suggested_reduce_quantity: number | null;
  stop_loss_price: number | null;
  take_profit_1: number | null;
  take_profit_2: number | null;
  hard_gate_status: string;
  key_reasons_json: string[];
  key_risks_json: string[];
  data_quality_status: string;
  requires_manual_review: boolean;
  advice_version: string;
  created_at: string;
}

export interface PositionRow {
  account_scope: string;
  stock_code: string;
  stock_name: string | null;
  quantity: number;
  available_quantity: number;
  cost_price: number;
  buy_date: string | null;
  source: string;
  version: string;
}

export interface PositionTruthStatus {
  status: "CONFIRMED_POSITIONS" | "CONFIRMED_EMPTY" | "MISSING" | "STALE" | "INVALID" | "CONFLICTED";
  snapshot_time?: string | null;
  human_count: number;
  ai_count: number;
  confirmed_empty: boolean;
  missing_fields: string[];
  action_run_allowed: boolean;
  scope_status: Record<string, string>;
  required_scopes: string[];
  maximum_snapshot_age_hours: number;
  ai_simulation_required: boolean;
  scope_details: Record<string, {
    required: boolean;
    status: string;
    snapshot_time?: string | null;
    position_count: number;
    confirmed_empty: boolean;
    stale: boolean;
    invalid_rows: string[];
  }>;
}
