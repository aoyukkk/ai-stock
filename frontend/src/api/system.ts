import { apiGet } from "./http";

export function getHealth() {
  return apiGet<Record<string, unknown>>("/health");
}

export function getConfigSummary() {
  return apiGet<Record<string, unknown>>("/api/v1/system/config-summary");
}

export function getDatabaseHealth() {
  return apiGet<Record<string, unknown>>("/api/v1/database/health");
}
