import { apiGet, apiPost } from "./http";

export function getLLMStatus() {
  return apiGet<Record<string, unknown>>("/api/v1/llm/status");
}

export function mockChat(payload: { agent_name: string; task: string; message: string }) {
  return apiPost<Record<string, unknown>>("/api/v1/llm/mock-chat", payload);
}

export function getUsageSummary() {
  return apiGet<Record<string, unknown>>("/api/v1/llm/usage-summary");
}
