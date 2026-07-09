import { apiGet, apiPost } from "./http";

export function runIntradayScan() {
  return apiPost<Record<string, unknown>>("/api/v1/alerts/intraday/scan");
}

export function getRecentAlerts(limit = 50) {
  return apiGet<Record<string, unknown>>("/api/v1/alerts/recent", { params: { limit } });
}

export function getAlertsConfig() {
  return apiGet<Record<string, unknown>>("/api/v1/alerts/config");
}
