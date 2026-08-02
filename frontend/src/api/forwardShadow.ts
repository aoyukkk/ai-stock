import { apiGet } from "@/api/http";
import type { ApiEnvelope } from "@/types/api";
import type { ForwardShadowRow, ForwardShadowSummary } from "@/types/forwardShadow";

export const getForwardShadowSummary = (asOfDate: string): Promise<ApiEnvelope<ForwardShadowSummary>> =>
  apiGet("/api/forward-shadow/summary", { params: { as_of_date: asOfDate } });
export const getForwardModelComparison = (asOfDate: string): Promise<ApiEnvelope<ForwardShadowRow[]>> =>
  apiGet("/api/forward-shadow/model-comparison", { params: { as_of_date: asOfDate } });
export const getForwardGates = (asOfDate: string): Promise<ApiEnvelope<ForwardShadowRow[]>> =>
  apiGet("/api/forward-shadow/gates", { params: { as_of_date: asOfDate } });
export const getForwardFactors = (asOfDate: string): Promise<ApiEnvelope<ForwardShadowRow[]>> =>
  apiGet("/api/forward-shadow/factors", { params: { as_of_date: asOfDate } });
export const getForwardPending = (asOfDate: string): Promise<ApiEnvelope<ForwardShadowRow[]>> =>
  apiGet("/api/forward-shadow/pending", { params: { as_of_date: asOfDate } });
