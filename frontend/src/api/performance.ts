import { apiGet, apiPost, apiPut } from "@/api/http";
import type { PerformancePage, PerformanceRequest, PerformanceSummary } from "@/types/performance";

const query = (values: Record<string, string | number | undefined | null>) => new URLSearchParams(
  Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== "").map(([key, value]) => [key, String(value)])
).toString();

export const performanceApi = {
  run: (payload: PerformanceRequest) => apiPost<Record<string, unknown>>("/api/workbench/performance/run", payload),
  summary: (runId?: string) => apiGet<PerformanceSummary>(`/api/workbench/performance/summary?${query({ performance_run_id: runId })}`),
  cohorts: (runId?: string, page = 1, pageSize = 50) => apiGet<PerformancePage<Record<string, unknown>>>(`/api/workbench/performance/cohorts?${query({ performance_run_id: runId, page, page_size: pageSize })}`),
  daily: (runId?: string, page = 1, pageSize = 50) => apiGet<PerformancePage<Record<string, unknown>>>(`/api/workbench/performance/daily?${query({ performance_run_id: runId, page, page_size: pageSize })}`),
  stocks: (runId?: string, page = 1, pageSize = 50, keyword = "") => apiGet<PerformancePage<Record<string, unknown>>>(`/api/workbench/performance/stocks?${query({ performance_run_id: runId, page, page_size: pageSize, keyword })}`),
  runs: () => apiGet<{ items: Record<string, unknown>[] }>("/api/workbench/performance/runs"),
  cacheStatus: (runId?: string) => apiGet<Record<string, unknown>>(`/api/workbench/performance/cache-status?${query({ performance_run_id: runId })}`),
  incremental: (runId: string, evaluationEndDate: string) => apiPost<Record<string, unknown>>("/api/workbench/performance/incremental-refresh", { performance_run_id: runId, evaluation_end_date: evaluationEndDate }),
  invalidate: (runId: string, reason: string) => apiPost<Record<string, unknown>>("/api/workbench/performance/invalidate", { performance_run_id: runId, reason }),
  methodology: () => apiGet<Record<string, unknown>>("/api/workbench/performance/methodology"),
  settings: () => apiGet<Record<string, unknown>>("/api/workbench/performance/settings"),
  updateSettings: (values: Record<string, unknown>) => apiPut<Record<string, unknown>>("/api/workbench/performance/settings", { values }),
  exportExcel: (runId: string) => apiPost<Record<string, unknown>>("/api/workbench/performance/export", { performance_run_id: runId })
};
