import { apiGet, apiPost } from "@/api/http";
import type { ApiEnvelope } from "@/types/api";
import type { EntryTimingFilters, EntryTimingRow, EntryTimingSummary, EntryTimingV22Filters, EntryTimingV22Row, EntryTimingV22Summary } from "@/types/entryTiming";

export function runEntryTimingShadow(tradeDate: string): Promise<ApiEnvelope<EntryTimingSummary>> {
  return apiPost("/api/workbench/entry-timing/run-shadow", {
    trade_date: tradeDate,
    candidate_mode: "QUANT_TOP100",
    confirm_shadow: true,
  });
}

export function getLatestEntryTiming(tradeDate: string): Promise<ApiEnvelope<EntryTimingSummary | null>> {
  return apiGet("/api/workbench/entry-timing/latest", { params: { trade_date: tradeDate } });
}

export function getEntryTimingResults(
  runId: string,
  page: number,
  pageSize: number,
  poolType?: "AI_POOL" | "MANUAL_CHALLENGE_POOL",
): Promise<ApiEnvelope<{ items: EntryTimingRow[]; total: number; page: number; page_size: number }>> {
  return apiGet("/api/workbench/entry-timing/results", {
    params: { run_id: runId, page, page_size: pageSize, ...(poolType ? { pool_type: poolType } : {}) },
  });
}

export function runEntryTimingV2Shadow(tradeDate: string): Promise<ApiEnvelope<EntryTimingSummary>> {
  return apiPost("/api/workbench/entry-timing/v2/run-shadow", {
    trade_date: tradeDate,
    candidate_mode: "QUANT_TOP100",
    confirm_shadow: true,
  });
}

export function getLatestEntryTimingV2(tradeDate: string): Promise<ApiEnvelope<EntryTimingSummary | null>> {
  return apiGet("/api/workbench/entry-timing/v2/latest", { params: { trade_date: tradeDate } });
}

export function getEntryTimingV2Results(
  runId: string,
  page: number,
  pageSize: number,
  filters: EntryTimingFilters,
): Promise<ApiEnvelope<{ items: EntryTimingRow[]; total: number; page: number; page_size: number }>> {
  return apiGet("/api/workbench/entry-timing/v2/results", {
    params: { run_id: runId, page, page_size: pageSize, ...filters },
  });
}

export function runEntryTimingV22Historical(start: string, end: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/entry-timing/v22/historical-shadow", {
    historical_start: start,
    historical_end: end,
    confirm_shadow: true,
  });
}

export function getLatestEntryTimingV22(tradeDate: string): Promise<ApiEnvelope<EntryTimingV22Summary | null>> {
  return apiGet("/api/workbench/entry-timing/v22/latest", { params: { trade_date: tradeDate } });
}

export function getEntryTimingV22Results(
  runId: string,
  page: number,
  pageSize: number,
  filters: EntryTimingV22Filters,
): Promise<ApiEnvelope<{ items: EntryTimingV22Row[]; total: number; page: number; page_size: number }>> {
  return apiGet("/api/workbench/entry-timing/v22/results", {
    params: { run_id: runId, page, page_size: pageSize, ...filters },
  });
}
