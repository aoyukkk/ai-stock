export interface DecisionExplainabilitySummary {
  run_id: string;
  trade_date: string;
  source_v2_run_id: string;
  quant_run_id: string;
  candidate_count: number;
  pass_core_count: number;
  pass_exploratory_count: number;
  review_count: number;
  reject_count: number;
  open_set_count: number;
  status: string;
  shadow_only: boolean;
  enabled_in_production: boolean;
  quant_hash_unchanged: boolean;
  flash_hash_unchanged: boolean;
  pro_hash_unchanged: boolean;
  llm_calls: number;
  external_api_calls: number;
  orders_created: number;
  version: string;
}

export interface DecisionExplainabilityRow extends Record<string, unknown> {
  stock_code: string;
  stock_name?: string;
  trade_date: string;
  quant_rank?: number;
  industry?: string;
  admission_state: "PASS_CORE" | "PASS_EXPLORATORY" | "REVIEW" | "REJECT";
  strategy_status: "PROBABILISTIC" | "OPEN_SET";
  strategy_probability: Record<string, number>;
  hard_gate_results: Record<string, boolean>;
  risk_penalties: Record<string, { score_penalty: number; position_multiplier: number }>;
  opportunity_components: Record<string, number>;
  portfolio_adjustments: Record<string, { score_penalty: number; position_multiplier: number }>;
  counterfactuals: Record<string, { admission_state: string; final_score: number; score_delta: number; position_multiplier: number }>;
  base_score: number;
  opportunity_score: number;
  final_score: number;
  expected_value_score?: number;
  risk_adjusted_opportunity_score?: number;
  position_multiplier: number;
  why_selected?: string;
  why_rejected: string[];
  largest_factor?: string;
  largest_gate?: string;
  shadow_only: boolean;
  version: string;
}

export interface FactorAttributionRow {
  factor_family: string;
  raw_signal: Record<string, number | null>;
  normalized_score: number;
  score_contribution: number;
  gate_contribution: number;
  rank_contribution: number;
  interaction_note: string;
}

export interface DecisionExplainabilityDetail extends DecisionExplainabilityRow {
  timing_contract: Record<string, unknown>;
  factor_attribution: FactorAttributionRow[];
}

export interface GateEvaluationRow {
  gate_name: string;
  blocked_count: number;
  affected_count: number;
  evaluated_count: number;
  future_return: number | null;
  avoided_loss: number;
  missed_gain: number;
  net_gate_value: number;
  counterfactual: Record<string, unknown>;
  version: string;
}

export interface FactorPerformanceRow {
  factor_family: string;
  sample_count: number;
  win_rate: number | null;
  avg_return_d1: number | null;
  avg_return_d3: number | null;
  avg_return_d5: number | null;
  avg_drawdown: number | null;
  positive_contribution_rate: number | null;
  negative_contribution_rate: number | null;
  period: string;
  version: string;
}

export interface GateValueRow {
  gate_name: string;
  trigger_count: number;
  blocked_count: number;
  future_return: number | null;
  avoided_loss: number;
  missed_gain: number;
  net_gate_value: number;
  false_positive_rate: number | null;
  version: string;
}
