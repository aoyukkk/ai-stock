import { apiGet, apiPost } from "./http";

export type RunMode =
  | "RESEARCH_ONLY"
  | "HISTORICAL_REPLAY"
  | "POST_MARKET_PRELIMINARY"
  | "POST_MARKET_FINAL"
  | "PRE_MARKET_RECHECK"
  | "INTRADAY_MONITOR";

export type TemporalStatus = "PASS" | "PASS_WITH_WARNINGS" | "PROVISIONAL" | "BLOCKED";

export interface DatasetWatermark {
  dataset_name: string;
  requested_trade_date?: string | null;
  latest_trade_date?: string | null;
  row_count: number;
  expected_count: number;
  coverage_ratio: number;
  is_complete: boolean;
  is_stale: boolean;
  source_status: string;
  error_category?: string | null;
}

export interface DataReadiness {
  decision_time: string;
  market_session: string;
  requested_run_mode: RunMode;
  base_market_trade_date?: string | null;
  target_trade_date?: string | null;
  latest_completed_trade_date?: string | null;
  dataset_watermarks: Record<string, DatasetWatermark>;
  temporal_status: TemporalStatus;
  actionable: boolean;
  block_reasons: string[];
  warnings: string[];
  allowed_alternative_modes: RunMode[];
  run_id: string;
  run_data_manifest_id: string;
}

export function getCurrentDataReadiness() {
  return apiGet<DataReadiness>("/api/v1/data-readiness/current");
}

export function checkDataReadiness(payload: {
  run_mode: RunMode;
  decision_time?: string;
  base_market_trade_date?: string;
  target_trade_date?: string;
  allow_provisional?: boolean;
}) {
  return apiPost<DataReadiness>("/api/v1/data-readiness/check", payload);
}
