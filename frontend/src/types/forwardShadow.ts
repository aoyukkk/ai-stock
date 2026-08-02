export interface ForwardShadowSummary {
  as_of_date: string;
  outcome_count: number;
  matured: Record<string, number>;
  pending: number;
  tradable: number;
  sample_status: string;
  fair_ab_matured_count: number;
  fair_ab_sample_status: string;
  promotion_recommendation: string;
  opaque_llm_contribution: boolean;
}

export type ForwardShadowRow = Record<string, unknown>;
