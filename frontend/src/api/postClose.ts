import { apiGet, apiPost } from "@/api/http";
import type { ApiEnvelope } from "@/types/api";
import type { PositionRow, PositionTruthStatus, PostCloseActionRow, PostCloseStatus } from "@/types/postClose";

export function runShadowEnhancement(tradeDate: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/ifind-enhancement/run-shadow", { trade_date: tradeDate });
}

export function getLatestEnhancement(tradeDate: string): Promise<ApiEnvelope<Record<string, unknown> | null>> {
  return apiGet("/api/workbench/ifind-enhancement/latest", { params: { trade_date: tradeDate } });
}

export function runPostCloseFast(tradeDate: string, runMode: "POST_CLOSE_FAST" | "POST_CLOSE_FINAL" = "POST_CLOSE_FAST", includeAiSimulation = false): Promise<ApiEnvelope<PostCloseStatus>> {
  return apiPost("/api/workbench/post-close-actions/run-fast", { trade_date: tradeDate, run_mode: runMode, include_ai_simulation: includeAiSimulation });
}

export function runPostClosePro(actionRunId: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/post-close-actions/run-pro-review", { action_run_id: actionRunId });
}

export function getPostCloseStatus(actionRunId?: string, tradeDate?: string): Promise<ApiEnvelope<PostCloseStatus>> {
  return apiGet("/api/workbench/post-close-actions/status", {
    params: {
      ...(actionRunId ? { action_run_id: actionRunId } : {}),
      ...(!actionRunId && tradeDate ? { trade_date: tradeDate } : {}),
    },
  });
}

export function getPostCloseResults(actionRunId: string, page: number, pageSize: number, heldOnly?: boolean): Promise<ApiEnvelope<{ items: PostCloseActionRow[]; total: number; page: number; page_size: number }>> {
  return apiGet("/api/workbench/post-close-actions/results", { params: { action_run_id: actionRunId, page, page_size: pageSize, held_only: heldOnly } });
}

export function getPostCloseHistory(tradeDate?: string): Promise<ApiEnvelope<{ items: PostCloseStatus[]; count: number }>> {
  return apiGet("/api/workbench/post-close-actions/history", { params: tradeDate ? { trade_date: tradeDate } : undefined });
}

export function getPostCloseCompare(actionRunId: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiGet("/api/workbench/post-close-actions/compare", { params: { action_run_id: actionRunId } });
}

export function exportPostClose(actionRunId: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/post-close-actions/export", { action_run_id: actionRunId });
}

export function getCurrentPositions(tradeDate?: string, includeAiSimulation = false): Promise<ApiEnvelope<{ items: PositionRow[]; count: number } & PositionTruthStatus>> {
  return apiGet("/api/workbench/positions/current", { params: { ...(tradeDate ? { trade_date: tradeDate } : {}), include_ai_simulation: includeAiSimulation } });
}

export function previewPositionImport(filename: string, contentBase64: string, accountScope = "HUMAN_REFERENCE"): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/positions/import-preview", { filename, content_base64: contentBase64, account_scope: accountScope });
}

export function confirmPositionImport(previewId: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/positions/import-confirm", { preview_id: previewId });
}

export function getPositionTruthStatus(tradeDate: string, includeAiSimulation = false): Promise<ApiEnvelope<PositionTruthStatus>> {
  return apiGet("/api/workbench/positions/truth-status", { params: { trade_date: tradeDate, include_ai_simulation: includeAiSimulation } });
}

export function confirmEmptyPositions(tradeDate: string, accountScopes: Array<"HUMAN_REFERENCE" | "AI_SIMULATION"> = ["HUMAN_REFERENCE"]): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiPost("/api/workbench/positions/confirm-empty", { trade_date: tradeDate, account_scopes: accountScopes, confirmed_by: "LOCAL_TRADER" });
}

export function getFastFinalCompare(tradeDate: string): Promise<ApiEnvelope<Record<string, unknown>>> {
  return apiGet("/api/workbench/post-close-actions/compare", { params: { trade_date: tradeDate } });
}
