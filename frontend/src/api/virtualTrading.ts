import { apiGet, apiPost } from "./http";

export function createDefaultAccount() {
  return apiPost<Record<string, unknown>>("/api/v1/virtual-trading/accounts/default");
}

export function getAccount(accountId?: number) {
  return apiGet<Record<string, unknown>>("/api/v1/virtual-trading/account", {
    params: { account_id: accountId }
  });
}

export function runPlans(params?: { input_top_n?: number; account_id?: number; persist_plans?: boolean }) {
  return apiPost<Record<string, unknown>>("/api/v1/virtual-trading/run-plans", undefined, { params });
}

export function getOrders(accountId?: number) {
  return apiGet<Record<string, unknown>>("/api/v1/virtual-trading/orders", {
    params: { account_id: accountId }
  });
}

export function getPositions(accountId?: number) {
  return apiGet<Record<string, unknown>>("/api/v1/virtual-trading/positions", {
    params: { account_id: accountId }
  });
}

export function getTrades(accountId?: number) {
  return apiGet<Record<string, unknown>>("/api/v1/virtual-trading/trades", {
    params: { account_id: accountId }
  });
}

export function cancelOrder(orderId: number, reason?: string) {
  return apiPost<Record<string, unknown>>(`/api/v1/virtual-trading/orders/${orderId}/cancel`, undefined, {
    params: { reason }
  });
}

export function repriceOrder(orderId: number, newPrice: number, reason?: string) {
  return apiPost<Record<string, unknown>>(`/api/v1/virtual-trading/orders/${orderId}/reprice`, {
    new_price: newPrice,
    reason
  });
}
