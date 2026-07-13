import { apiDelete, apiGet, apiPost, apiPut } from "@/api/http";
import type { AvailableTradeDate, ManualSelection, PageResult, PipelineJob, WorkbenchStatus } from "@/types/workbench";

const dateQuery = (tradeDate: string) => `trade_date=${encodeURIComponent(tradeDate)}`;

export const workbenchApi = {
  availableDates: (signal?: AbortSignal) => apiGet<{ items: AvailableTradeDate[] }>("/api/workbench/available-dates", { signal }),
  status: (tradeDate: string, pipelineRunId?: string | null, signal?: AbortSignal) => apiGet<WorkbenchStatus>(`/api/workbench/status?${dateQuery(tradeDate)}${pipelineRunId ? `&pipeline_run_id=${encodeURIComponent(pipelineRunId)}` : ""}`, { signal }),
  loadExisting: (tradeDate: string, pipelineRunId?: string | null) => apiPost<WorkbenchStatus>("/api/workbench/runs/load-existing", { trade_date: tradeDate, pipeline_run_id: pipelineRunId || null }),
  dataCheck: (tradeDate: string) => apiPost<Record<string, unknown>>("/api/workbench/data/check", { trade_date: tradeDate }),
  settings: () => apiGet<Record<string, unknown>>("/api/workbench/settings"),
  updateSettings: (values: Record<string, unknown>) => apiPut<Record<string, unknown>>("/api/workbench/settings", { values }),
  secretStatus: () => apiGet<Record<string, { configured: boolean; last_test_status: string }>>("/api/workbench/secrets/status"),
  setSecret: (provider: string, value: string) => apiPost(`/api/workbench/secrets/${provider}`, { value }),
  testSecret: (provider: string) => apiPost<{ status: string }>(`/api/workbench/secrets/${provider}/test`),
  run: (type: "data" | "quant" | "flash" | "final" | "export", tradeDate: string) => apiPost<PipelineJob>(`/api/workbench/${type}/run`, { trade_date: tradeDate, mode: "USE_EXISTING" }),
  jobs: (tradeDate: string, page = 1, pageSize = 20) => apiGet<PageResult<PipelineJob & Record<string, unknown>>>(`/api/workbench/jobs?${dateQuery(tradeDate)}&page=${page}&page_size=${pageSize}`),
  quant: (tradeDate: string, page = 1, pageSize = 50, pipelineRunId?: string | null, signal?: AbortSignal) => apiGet<PageResult<Record<string, unknown>>>(`/api/workbench/quant/results?${dateQuery(tradeDate)}&page=${page}&page_size=${pageSize}${pipelineRunId ? `&pipeline_run_id=${encodeURIComponent(pipelineRunId)}` : ""}`, { signal }),
  flash: (tradeDate: string, page = 1, pageSize = 50, pipelineRunId?: string | null, signal?: AbortSignal, keyword = "", sortBy = "rank", sortOrder = "asc") => apiGet<PageResult<Record<string, unknown>>>(`/api/workbench/flash/results?${dateQuery(tradeDate)}&page=${page}&page_size=${pageSize}${pipelineRunId ? `&pipeline_run_id=${encodeURIComponent(pipelineRunId)}` : ""}&keyword=${encodeURIComponent(keyword)}&sort_by=${encodeURIComponent(sortBy)}&sort_order=${encodeURIComponent(sortOrder)}`, { signal }),
  final: (tradeDate: string, pipelineRunId?: string | null, signal?: AbortSignal) => apiGet<{ items: Record<string, unknown>[] }>(`/api/workbench/final/results?${dateQuery(tradeDate)}${pipelineRunId ? `&pipeline_run_id=${encodeURIComponent(pipelineRunId)}` : ""}`, { signal }),
  orders: (tradeDate: string, pipelineRunId?: string | null, signal?: AbortSignal) => apiGet<{ items: Record<string, unknown>[] }>(`/api/workbench/order-position/results?${dateQuery(tradeDate)}${pipelineRunId ? `&pipeline_run_id=${encodeURIComponent(pipelineRunId)}` : ""}`, { signal }),
  fundamentals: (tradeDate: string, pipelineRunId?: string | null, signal?: AbortSignal) => apiGet<{ items: Record<string, unknown>[] }>(`/api/workbench/fundamentals/results?${dateQuery(tradeDate)}${pipelineRunId ? `&pipeline_run_id=${encodeURIComponent(pipelineRunId)}` : ""}`, { signal }),
  manualSnapshot: (tradeDate: string, pipelineRunId?: string | null, signal?: AbortSignal) => apiGet<{ items: Record<string, unknown>[] }>(`/api/workbench/manual-selections/snapshot?${dateQuery(tradeDate)}${pipelineRunId ? `&pipeline_run_id=${encodeURIComponent(pipelineRunId)}` : ""}`, { signal }),
  manual: (tradeDate: string) => apiGet<{ items: ManualSelection[] }>(`/api/workbench/manual-selections?${dateQuery(tradeDate)}`),
  addManual: (payload: { trade_date: string; stock_code: string; reason: string; priority: string; quant_run_id?: string | null }) => apiPost<ManualSelection>("/api/workbench/manual-selections", payload),
  addManualBatch: (payload: { trade_date: string; stock_codes: string[]; reason: string; priority: string; quant_run_id?: string | null }) => apiPost<{ items: ManualSelection[]; count: number }>("/api/workbench/manual-selections/batch", payload),
  updateManual: (id: number, payload: { reason: string; priority: string }) => apiPut<ManualSelection>(`/api/workbench/manual-selections/${id}`, payload),
  deleteManual: (id: number) => apiDelete(`/api/workbench/manual-selections/${id}`),
  clearManual: (tradeDate: string) => apiDelete(`/api/workbench/manual-selections?${dateQuery(tradeDate)}`)
};
