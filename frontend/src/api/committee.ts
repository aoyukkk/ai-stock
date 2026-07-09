import { apiGet } from "./http";

export function runCommittee(params?: { input_top_n?: number; final_top_n?: number; persist?: boolean }) {
  return apiGet<Record<string, unknown>>("/api/v1/committee/run", { params });
}

export function getCommitteeConfig() {
  return apiGet<Record<string, unknown>>("/api/v1/committee/config");
}
