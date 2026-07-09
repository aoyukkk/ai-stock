import { apiGet } from "./http";

export function runQuantScan(params?: { top_q?: number; persist?: boolean }) {
  return apiGet<Record<string, unknown>>("/api/v1/quant/scan", { params });
}

export function getQuantConfig() {
  return apiGet<Record<string, unknown>>("/api/v1/quant/config");
}
