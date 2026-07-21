export interface PerformanceRequest {
  evaluation_end_date: string;
  lookback_value: number;
  lookback_unit: "TRADING_DAYS" | "CUSTOM";
  start_selection_date: string | null;
  end_selection_date: string | null;
  return_basis: "NEXT_OPEN" | "SIGNAL_CLOSE";
  selection_scope: "FINAL_CANDIDATES" | "KEY_CANDIDATES" | "LLM_ONLY" | "MANUAL_ONLY" | "BOTH_ONLY" | "NON_ZERO_POSITION" | "ALL_CANDIDATES_INCLUDING_ZERO_POSITION";
  weighting_mode: "EQUAL_WEIGHT" | "SUGGESTED_POSITION_WEIGHT";
  include_zero_position_stocks: boolean;
  include_risk_blocked_stocks: boolean;
  force_recalculate: boolean;
}

export interface PerformanceSummary extends Record<string, unknown> {
  performance_run_id?: string;
  status: string;
  cohort_count: number;
  stock_count: number;
  average_daily_return?: number | null;
  average_cumulative_return?: number | null;
  positive_portfolio_ratio?: number | null;
  positive_stock_ratio?: number | null;
  best_selection_date?: string | null;
  worst_selection_date?: string | null;
  max_drawdown?: number | null;
  coverage_ratio?: number;
  latest_evaluation_date?: string;
  cache_status?: string;
}

export interface PerformancePage<T extends Record<string, unknown>> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}
