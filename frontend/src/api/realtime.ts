import { apiDelete, apiGet, apiPost, apiPut } from "@/api/http";
import type { ApiEnvelope } from "@/types/api";
import type { AcceptanceRunSummary, IndexRow, MonitorAlert, MonitorPoolCandidate, MonitorPoolItem, MonitorSession, RealtimeProviderStatus, RealtimeRow, RefreshResult, SelectedMonitorItem } from "@/types/realtime";

export function getRealtimeProviderStatus(): Promise<ApiEnvelope<RealtimeProviderStatus>> {
  return apiGet("/api/workbench/realtime/provider-status");
}

export function getRealtimePool(tradeDate: string): Promise<ApiEnvelope<{ items: MonitorPoolItem[]; count: number; pool_hash: string }>> {
  return apiGet("/api/workbench/realtime/pool", { params: { trade_date: tradeDate } });
}

export function refreshRealtime(scope: "ALL" | "INDEX" | "STOCKS", stockCodes: string[] = [], force = false): Promise<ApiEnvelope<RefreshResult>> {
  return apiPost("/api/workbench/realtime/refresh", { scope, stock_codes: stockCodes, force });
}

export function getRealtimeResults(page = 1, pageSize = 50): Promise<ApiEnvelope<{ items: RealtimeRow[]; total: number; page: number; page_size: number }>> {
  return apiGet("/api/workbench/realtime/results", { params: { page, page_size: pageSize } });
}

export function getRealtimeIndices(tradeDate?: string): Promise<ApiEnvelope<{ items: IndexRow[] }>> {
  return apiGet("/api/workbench/realtime/indices", { params: tradeDate ? { trade_date: tradeDate } : undefined });
}

export function getMinuteBars(stockCode: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiGet("/api/workbench/realtime/minute-bars", { params: { stock_code: stockCode } });
}

export function getLatestAcceptance(): Promise<ApiEnvelope<AcceptanceRunSummary | null>> {
  return apiGet("/api/workbench/realtime/acceptance/latest");
}

export function runAcceptance(mode: "closed-session" | "open-session", body: {
  confirm_real_ifind: boolean;
  trade_date?: string;
  stock_limit?: number;
  minute_stock_count?: number;
  rounds?: number;
  interval_seconds?: number;
  max_external_calls?: number;
  force_provider_refresh?: boolean;
}): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost(`/api/workbench/realtime/acceptance/${mode}`, body);
}

export function getAcceptanceReport(runId: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiGet(`/api/workbench/realtime/acceptance/report/${encodeURIComponent(runId)}`);
}

export function getIfindUsage(): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiGet("/api/data-sources/ifind/usage");
}

const monitorBase = "/api/workbench/intraday-monitor";

export function createMonitorSession(tradeDate: string): Promise<ApiEnvelope<MonitorSession>> { return apiPost(`${monitorBase}/sessions`, { trade_date: tradeDate }); }
export function getCurrentMonitorSession(tradeDate?: string): Promise<ApiEnvelope<MonitorSession | null>> { return apiGet(`${monitorBase}/sessions/current`, { params: tradeDate ? { trade_date: tradeDate } : undefined }); }
export function transitionMonitorSession(id: number, action: "start" | "pause" | "resume" | "stop"): Promise<ApiEnvelope<MonitorSession>> { return apiPost(`${monitorBase}/sessions/${id}/${action}`, {}); }
export function getSelectedMonitorPool(sessionId: number, page = 1, pageSize = 50): Promise<ApiEnvelope<{ items: SelectedMonitorItem[]; total: number }>> { return apiGet(`${monitorBase}/pool`, { params: { monitor_session_id: sessionId, page, page_size: pageSize } }); }
export function previewMonitorPool(sessionId: number, items: MonitorPoolCandidate[], source = "MANUAL_CONFIRMATION", sourceMiddayRunId?: string): Promise<ApiEnvelope<Record<string, unknown>>> { return apiPost(`${monitorBase}/pool/preview`, { monitor_session_id: sessionId, items, source, source_midday_run_id: sourceMiddayRunId }); }
export function confirmMonitorPool(sessionId: number, items: MonitorPoolCandidate[], source = "MANUAL_CONFIRMATION", sourceMiddayRunId?: string): Promise<ApiEnvelope<Record<string, unknown>>> { return apiPost(`${monitorBase}/pool/confirm`, { monitor_session_id: sessionId, items, source, source_midday_run_id: sourceMiddayRunId, confirmed_by: "LOCAL_TRADER" }); }
export function updateMonitorItem(id: number, body: Record<string, unknown>): Promise<ApiEnvelope<SelectedMonitorItem>> { return apiPut(`${monitorBase}/pool/items/${id}`, body); }
export function removeMonitorItem(id: number): Promise<ApiEnvelope<Record<string, unknown>>> { return apiDelete(`${monitorBase}/pool/items/${id}`); }
export function getMiddayMonitorSuggestions(tradeDate: string): Promise<ApiEnvelope<{ run_id: string | null; items: MonitorPoolCandidate[]; count: number }>> { return apiGet(`${monitorBase}/midday-suggestions`, { params: { trade_date: tradeDate } }); }
export function refreshSelectedMonitor(sessionId: number): Promise<ApiEnvelope<Record<string, unknown>>> { return apiPost(`${monitorBase}/refresh`, undefined, { params: { monitor_session_id: sessionId } }); }
export function getSelectedMonitorResults(sessionId: number, page = 1, pageSize = 50): Promise<ApiEnvelope<{ items: SelectedMonitorItem[]; total: number }>> { return apiGet(`${monitorBase}/results`, { params: { monitor_session_id: sessionId, page, page_size: pageSize } }); }
export function getMonitorAlerts(sessionId: number, page = 1, pageSize = 50): Promise<ApiEnvelope<{ items: MonitorAlert[]; total: number }>> { return apiGet(`${monitorBase}/alerts`, { params: { monitor_session_id: sessionId, page, page_size: pageSize } }); }
export function getMonitorUnreadCount(sessionId: number): Promise<ApiEnvelope<{ unread: number; warning: number; critical: number }>> { return apiGet(`${monitorBase}/alerts/unread-count`, { params: { monitor_session_id: sessionId } }); }
export function actOnMonitorAlert(id: number, action: "acknowledge" | "mute" | "resolve" | "dismiss", minutes = 15): Promise<ApiEnvelope<MonitorAlert>> { return apiPost(`${monitorBase}/alerts/${id}/${action}`, { operated_by: "LOCAL_TRADER", minutes }); }
export function explainMonitorAlert(id: number): Promise<ApiEnvelope<Record<string, unknown>>> { return apiPost(`${monitorBase}/alerts/${id}/explain`, {}); }
export function getMonitorSessionHistory(page = 1, pageSize = 20): Promise<ApiEnvelope<{ items: MonitorSession[]; total: number }>> { return apiGet(`${monitorBase}/sessions/history`, { params: { page, page_size: pageSize } }); }
export function getMonitorRules(sessionId: number): Promise<ApiEnvelope<{ items: Record<string, unknown>[] }>> { return apiGet(`${monitorBase}/rules`, { params: { monitor_session_id: sessionId } }); }
export function getMonitorUsage(sessionId?: number): Promise<ApiEnvelope<{ items: Record<string, unknown>[]; broker: Record<string, unknown> }>> { return apiGet(`${monitorBase}/usage`, { params: sessionId ? { monitor_session_id: sessionId } : undefined }); }
