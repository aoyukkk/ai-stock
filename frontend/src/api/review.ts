import { apiGet, apiPost } from "./http";

export function runDailyReview(params?: { date?: string; account_id?: number; use_mock_llm?: boolean }) {
  return apiPost<Record<string, unknown>>("/api/v1/review/daily/run", params || {});
}

export function getDailyReview(date: string) {
  return apiGet<Record<string, unknown>>(`/api/v1/review/daily/${date}`);
}

export function evaluatePredictions(params?: { date?: string }) {
  return apiPost<Record<string, unknown>>("/api/v1/review/predictions/evaluate", params || {});
}

export function evaluateOrderPlans(params?: { date?: string }) {
  return apiPost<Record<string, unknown>>("/api/v1/review/order-plans/evaluate", params || {});
}

export function getReviewConfig() {
  return apiGet<Record<string, unknown>>("/api/v1/review/config");
}
