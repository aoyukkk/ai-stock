import { apiGet } from "./http";

export function getDataSourceStatus() {
  return apiGet<Record<string, unknown>>("/api/v1/data-sources/status");
}

export function getMockStocks() {
  return apiGet<Record<string, unknown>>("/api/v1/data-sources/mock/stocks");
}

export function getMockQuotes(stockCodes?: string[]) {
  return apiGet<Record<string, unknown>>("/api/v1/data-sources/mock/quotes", {
    params: { stock_codes: stockCodes?.join(",") }
  });
}
