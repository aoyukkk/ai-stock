import type { PageResult, PipelineJob } from "@/types/workbench";

export interface MarketReviewBundle {
  run: Record<string, any>;
  snapshot: Record<string, any>;
  review: Record<string, any>;
  regime: Record<string, any>;
  outlook: Record<string, any>;
  search: Record<string, any>;
  evidence: Record<string, any>[];
  drivers: Record<string, any>[];
  scenarios: Record<string, any>[];
}

export interface MarketReviewRequest {
  trade_date: string;
  mode: "FULL" | "DATA_ONLY" | "REFRESH_EVIDENCE" | "REGENERATE_SUMMARY";
  force?: boolean;
  allow_real_pro?: boolean;
  allow_real_search?: boolean;
}

export type MarketReviewPage = PageResult<Record<string, any>>;
export type MarketReviewJob = PipelineJob;
