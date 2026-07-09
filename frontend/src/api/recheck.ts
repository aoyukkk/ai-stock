import { apiGet, apiPost } from "./http";

export function runPreMarketRecheck(params?: { limit?: number }) {
  return apiPost<Record<string, unknown>>("/api/v1/recheck/pre-market/run", undefined, { params });
}

export function recheckOrderPlan(orderPlanId: number) {
  return apiPost<Record<string, unknown>>(`/api/v1/recheck/order-plans/${orderPlanId}`);
}

export function getRecheckConfig() {
  return apiGet<Record<string, unknown>>("/api/v1/recheck/config");
}
