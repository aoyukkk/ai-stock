import { apiGet } from "./http";

export function getOrderPricePlans(params?: { input_top_n?: number; persist?: boolean }) {
  return apiGet<Record<string, unknown>>("/api/v1/order-price/plans", { params });
}

export function getOrderPriceConfig() {
  return apiGet<Record<string, unknown>>("/api/v1/order-price/config");
}
