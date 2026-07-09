import { apiGet } from "./http";

export function runLightScreening(params?: { quant_top_q?: number; top_n?: number; persist?: boolean }) {
  return apiGet<Record<string, unknown>>("/api/v1/screening/light/run", { params });
}

export function getLightScreeningConfig() {
  return apiGet<Record<string, unknown>>("/api/v1/screening/light/config");
}
