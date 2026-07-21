import { apiGet, apiPost } from "@/api/http";
import type { ApiEnvelope } from "@/types/api";
import type { MiddayResult, MiddayStatus } from "@/types/midday";

export function runMidday(tradeDate: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/midday/run", { trade_date: tradeDate, historical_validation: false, force: false });
}

export function getMiddayStatus(tradeDate: string): Promise<ApiEnvelope<MiddayStatus>> {
  return apiGet("/api/workbench/midday/status", { params: { trade_date: tradeDate } });
}

export function getMiddayResults(runId: string, page: number, pageSize: number): Promise<ApiEnvelope<{ items: MiddayResult[]; total: number; page: number; page_size: number }>> {
  return apiGet("/api/workbench/midday/results", { params: { run_id: runId, page, page_size: pageSize } });
}

export function recheckMidday(runId: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/midday/recheck", { run_id: runId });
}

export function exportMidday(runId: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/midday/export", { run_id: runId });
}

export function runMiddayV22(tradeDate: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/midday/v22/run", { trade_date: tradeDate, cutoff_time: "11:30:00" });
}

export function getMiddayV22Status(tradeDate: string): Promise<ApiEnvelope<MiddayStatus>> {
  return apiGet("/api/workbench/midday/v22/status", { params: { trade_date: tradeDate } });
}

export function getMiddayV22Results(runId: string, page: number, pageSize: number): Promise<ApiEnvelope<{ items: MiddayResult[]; total: number; page: number; page_size: number }>> {
  return apiGet("/api/workbench/midday/v22/results", { params: { run_id: runId, page, page_size: pageSize } });
}

export function recheckMiddayV22(tradeDate: string, stocks: string[] = []): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/midday/v22/recheck", { trade_date: tradeDate, stocks });
}
