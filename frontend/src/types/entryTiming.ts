export type AdmissionStatus = "PASS" | "REVIEW" | "BLOCK" | "DATA_INSUFFICIENT";

export interface EntryTimingSummary {
  run_id: string;
  trade_date: string;
  quant_run_id: string;
  status: string;
  candidate_count: number;
  pass_count: number;
  review_count: number;
  block_count: number;
  insufficient_count: number;
  admitted_count: number;
  manual_challenge_count: number;
  model_top20_count: number;
  entry_filtered_top20_count: number;
  final_shadow_pool_count: number;
  shadow_only: boolean;
  enabled_in_production: boolean;
  quant_hash_unchanged: boolean;
  llm_calls: number;
  external_api_calls: number;
  order_creation_count?: number;
  market_emotion_score?: number | null;
  market_emotion_state?: string;
  market_regime?: string;
  strategy_distribution?: Record<string, number>;
  flash_hash_unchanged?: boolean;
  pro_hash_unchanged?: boolean;
  versions?: Record<string, string>;
}

export interface EntryTimingRow {
  stock_code: string;
  stock_name?: string;
  quant_rank?: number;
  quant_score: number;
  flash_score?: number;
  position_score: number;
  pullback_score: number;
  volume_price_score: number;
  sector_score: number;
  market_score: number;
  liquidity_score: number;
  entry_timing_score: number;
  data_quality_score: number;
  admission_status: AdmissionStatus;
  risk_flags: string[];
  block_reasons: string[];
  pool_type: "AI_POOL" | "MANUAL_CHALLENGE_POOL";
  strategy_id?: string;
  strategy_fit_score?: number;
  market_emotion_score?: number | null;
  market_emotion_state?: string;
  market_regime?: string;
  market_gate_status?: string;
  entry_timing_v1_score?: number;
  entry_timing_v2_score?: number | null;
  admission_ranking_score_v2?: number | null;
  admission_status_v1?: AdmissionStatus;
  admission_status_v2?: AdmissionStatus;
  review_reasons?: string[];
  selection_source?: string;
  data_coverage?: Record<string, unknown>;
  version?: string;
}

export interface EntryTimingFilters {
  strategy_id?: string;
  emotion_state?: string;
  market_regime?: string;
  admission_status_v1?: string;
  admission_status_v2?: string;
  pool_type?: "AI_POOL" | "MANUAL_CHALLENGE_POOL";
}

export interface EntryTimingV22Summary {
  run_id: string;
  trade_date: string;
  regime_state: string;
  candidate_before: number;
  after_regime: number;
  after_concentration: number;
  triggered_count: number;
  shadow_only: boolean;
  llm_calls: number;
  external_calls: number;
  orders_created: number;
  source_hashes: Record<string, string>;
}

export interface EntryTimingV22Row {
  run_id: string;
  trade_date: string;
  stock_code: string;
  stock_name?: string;
  pool_type: "AI_POOL" | "MANUAL_CHALLENGE_POOL";
  industry?: string;
  cluster_id?: string;
  strategy_id: string;
  admission_score?: number;
  regime_state: string;
  deployment_status: string;
  deployment_reason: string;
  position_multiplier: number;
  crowding_status: string;
  crowding_reason: string;
  retained_rank_in_sector?: number;
  industry_candidate_count_before: number;
  industry_candidate_count_after: number;
  industry_pool_ratio?: number;
  trigger_status: string;
  trigger_reasons_json: string[];
  trigger_scores_json: Record<string, number>;
  version: string;
}

export interface EntryTimingV22Filters {
  regime_state?: string;
  deployment_status?: string;
  crowding_status?: string;
  trigger_status?: string;
  pool_type?: "AI_POOL" | "MANUAL_CHALLENGE_POOL";
}
