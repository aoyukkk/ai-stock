export type RunStatus = "EMPTY" | "NOT_RUN" | "PENDING" | "RUNNING" | "CANCELLATION_REQUESTED" | "SUCCESS" | "PARTIAL_SUCCESS" | "FAILED" | "CANCELLED" | "READY" | "LOADED" | "COMPLETED" | "BLOCKED" | "NOT_READY" | "WARNING";

export interface StageStatus {
  status: RunStatus;
  run_id: string | null;
  updated_at: string | null;
  count: number;
  success_count?: number;
  failure_count?: number;
  temporal_gate?: string;
  coverage?: number;
}

export interface WorkbenchStatus {
  trade_date: string;
  source_mode: "DATABASE" | "MOCK" | "EMPTY";
  pipeline_status: RunStatus;
  pipeline_run_id: string | null;
  candidate_set_hash: string | null;
  data: StageStatus;
  quant: StageStatus;
  flash: StageStatus;
  manual: StageStatus;
  final: StageStatus;
  export: StageStatus;
  counts: Record<string, number>;
  consistency: { status: "PASS" | "WARNING" | "EMPTY"; differences: Record<string, unknown>[] };
  manual_count: number;
  token: { used: number; limit: number; remaining: number; usage_ratio: number; unavailable_usage_count: number };
  flash_budget: { used: number; limit: number; remaining: number; usage_ratio: number };
  providers: Record<string, { configured: boolean }>;
  database: { status: string };
  mode: string;
  real_trading_enabled: boolean;
  latest_run_ids: Record<string, string | null>;
}

export interface AvailableTradeDate {
  trade_date: string;
  pipeline_status: RunStatus;
  pipeline_run_count: number;
  latest_completed_at: string;
}

export interface PageResult<T extends Record<string, unknown>> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  run_id?: string;
}

export interface PipelineJob {
  job_id: string;
  job_type: string;
  trade_date: string;
  status: RunStatus;
  stage: string;
  progress_current: number;
  progress_total: number;
  current_stock?: string | null;
  success_count: number;
  failure_count: number;
  token_usage: number;
  cost_usd: number;
  started_at: string | null;
  finished_at: string | null;
  error_code?: string | null;
  error_message?: string | null;
  run_ids: Record<string, string | null>;
  output_path?: string | null;
  checkpoint?: Record<string, unknown>;
}

export interface ManualSelection {
  id: number;
  stock_code: string;
  reason: string;
  priority: "HIGH" | "MEDIUM" | "LOW";
  selected_by: string;
  created_at: string;
  updated_at: string;
}

export interface TableColumn {
  key: string;
  label: string;
  minWidth?: number;
  formatter?: (value: unknown, row: Record<string, unknown>) => string;
}
