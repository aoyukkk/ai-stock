import { apiGet, apiPost } from "@/api/http";
import type { MarketReviewBundle, MarketReviewJob, MarketReviewPage, MarketReviewRequest } from "@/types/marketReview";

const base = "/api/workbench/market-review";
const dateQuery = (tradeDate: string) => `trade_date=${encodeURIComponent(tradeDate)}`;

export const marketReviewApi = {
  latest: (tradeDate: string, signal?: AbortSignal) => apiGet<MarketReviewBundle>(`${base}/latest?${dateQuery(tradeDate)}`, { signal }),
  history: (tradeDate: string, page = 1, pageSize = 20) => apiGet<MarketReviewPage>(`${base}/history?${dateQuery(tradeDate)}&page=${page}&page_size=${pageSize}`),
  evidence: (tradeDate: string, page = 1, pageSize = 20) => apiGet<MarketReviewPage>(`${base}/evidence?${dateQuery(tradeDate)}&page=${page}&page_size=${pageSize}`),
  drivers: (tradeDate: string, page = 1, pageSize = 20) => apiGet<MarketReviewPage>(`${base}/drivers?${dateQuery(tradeDate)}&page=${page}&page_size=${pageSize}`),
  run: (payload: MarketReviewRequest) => apiPost<MarketReviewJob>(`${base}/run`, payload),
  refreshEvidence: (payload: MarketReviewRequest) => apiPost<MarketReviewJob>(`${base}/evidence/refresh`, payload),
  regenerateSummary: (payload: MarketReviewRequest) => apiPost<MarketReviewJob>(`${base}/summary/regenerate`, payload),
  export: (payload: MarketReviewRequest) => apiPost<MarketReviewJob>(`${base}/export`, payload),
  methodology: () => apiGet<Record<string, unknown>>(`${base}/methodology`)
};
