import { apiGet, apiPost } from "./http";

export type SourceStatus =
  | "VERIFIED_STRUCTURED"
  | "DERIVED_RULE"
  | "WEB_VERIFIED"
  | "MANUAL_EVIDENCE"
  | "LLM_UNVERIFIED"
  | "UNKNOWN";

export interface ProvenancedField<T = unknown> {
  value: T;
  source_status: SourceStatus;
  display_marker: "" | "*";
  verified: boolean;
  confidence: number;
  fallback_reason?: string | null;
  requires_manual_review: boolean;
}

export interface FundamentalRunRequest {
  quant_run_id?: string;
  stock_codes?: string[];
  sample_mode?: "STRATIFIED" | "TOP_N";
  sample_size?: number;
  research_mode?: "DEEPSEEK_UNVERIFIED";
  dry_run?: boolean;
  use_real_llm?: boolean;
}

export const unverifiedMarkerHelp =
  "* 表示DeepSeek基于现有数据和模型知识形成的未核验推断，不代表已完成联网查证，仅供交易员观察。";

export function runFundamentalResearch(payload: FundamentalRunRequest) {
  return apiPost("/api/v1/fundamental-research/run", payload);
}

export function getFundamentalProfile(stockCode: string) {
  return apiGet(`/api/v1/fundamental-research/profiles/${encodeURIComponent(stockCode)}`);
}
