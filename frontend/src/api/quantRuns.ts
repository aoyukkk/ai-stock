import { apiGet } from "./http";

export function getLatestQuantRun() {
  return apiGet<Record<string, unknown>>("/api/pools/quant-runs/latest");
}

export function getQuantRunRanking(runId: string) {
  return apiGet<Record<string, unknown>>(`/api/pools/quant-runs/${encodeURIComponent(runId)}/ranking`);
}
