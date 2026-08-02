import { apiGet } from "@/api/http";

export const EVENT_OVERLAY_SCREENING_VERSION = "LLM_SCREENING_V3_1_FRESH_EVENT_OVERLAY_SHADOW";

const query = (tradeDate: string) =>
  `trade_date=${encodeURIComponent(tradeDate)}&screening_version=${encodeURIComponent(EVENT_OVERLAY_SCREENING_VERSION)}`;

export const eventOverlayApi = {
  summary: (tradeDate: string) => apiGet<Record<string, unknown>>(`/api/event-overlay/summary?${query(tradeDate)}`),
  top20: (tradeDate: string) => apiGet<{ items: Record<string, unknown>[] }>(`/api/event-overlay/top20?${query(tradeDate)}`),
  comparison: (tradeDate: string) => apiGet<{ items: Record<string, unknown>[] }>(`/api/event-overlay/comparison?${query(tradeDate)}`),
  evidence: (tradeDate: string) => apiGet<{ snapshots: Record<string, unknown>[]; items: Record<string, unknown>[] }>(`/api/event-overlay/evidence?${query(tradeDate)}`),
  dataQuality: (tradeDate: string) => apiGet<{ items: Record<string, unknown>[] }>(`/api/event-overlay/data-quality?${query(tradeDate)}`),
};
