import { apiGet } from "@/api/http";

export const QUANT_FACTOR_VERSION = "TUSHARE_QUANT_V2_CORRECTED_SHADOW";
export const SCREENING_VERSIONS = [
  "FLASH_V2_STRUCTURED_LIGHT_SCREENING_V5",
  "LLM_SCREENING_V3_1_FRESH_EVENT_OVERLAY_SHADOW",
] as const;
export const FULL_UNIVERSE_EVALUATION_VERSION =
  "FULL_UNIVERSE_QUANT_EFFECTIVENESS_V1";

export interface ModelEffectivenessQuery {
  quantFactorVersion: string;
  screeningVersion: string;
  horizon?: number;
  actionableOnly?: boolean;
  startDate?: string;
  endDate?: string;
}

const query = (value: ModelEffectivenessQuery) => {
  const params = new URLSearchParams({
    quant_factor_version: value.quantFactorVersion,
    screening_version: value.screeningVersion,
  });
  if (value.horizon) params.set("horizon", String(value.horizon));
  if (value.actionableOnly) params.set("actionable_only", "true");
  if (value.startDate) params.set("start_date", value.startDate);
  if (value.endDate) params.set("end_date", value.endDate);
  return params.toString();
};

export const modelEffectivenessApi = {
  summary: (value: ModelEffectivenessQuery) =>
    apiGet<Record<string, unknown>>(`/api/model-effectiveness/summary?${query(value)}`),
  dailyMetrics: (value: ModelEffectivenessQuery) =>
    apiGet<Record<string, unknown>[]>(`/api/model-effectiveness/daily-metrics?${query(value)}`),
  details: (value: ModelEffectivenessQuery) =>
    apiGet<Record<string, unknown>[]>(`/api/model-effectiveness/details?${query(value)}`),
  dataQuality: (value: ModelEffectivenessQuery) =>
    apiGet<Record<string, unknown>[]>(`/api/model-effectiveness/data-quality?${query(value)}`),
  fullUniverseSummary: (horizon = 3) =>
    apiGet<Record<string, unknown>>(
      `/api/model-effectiveness/full-universe/summary?factor_version=${QUANT_FACTOR_VERSION}` +
        `&evaluation_version=${FULL_UNIVERSE_EVALUATION_VERSION}&horizon=${horizon}`,
    ),
  fullUniverseIc: (horizon = 3) =>
    apiGet<Record<string, unknown>[]>(
      `/api/model-effectiveness/full-universe/ic?factor_version=${QUANT_FACTOR_VERSION}` +
        `&evaluation_version=${FULL_UNIVERSE_EVALUATION_VERSION}&horizon=${horizon}`,
    ),
  fullUniverseDeciles: (horizon = 3) =>
    apiGet<Record<string, unknown>[]>(
      `/api/model-effectiveness/full-universe/deciles?factor_version=${QUANT_FACTOR_VERSION}` +
        `&evaluation_version=${FULL_UNIVERSE_EVALUATION_VERSION}&horizon=${horizon}`,
    ),
  fullUniverseFixedBands: (horizon = 3) =>
    apiGet<Record<string, unknown>[]>(
      `/api/model-effectiveness/full-universe/fixed-bands?factor_version=${QUANT_FACTOR_VERSION}` +
        `&evaluation_version=${FULL_UNIVERSE_EVALUATION_VERSION}&horizon=${horizon}`,
    ),
  fullUniverseHeadBands: (horizon = 3) =>
    apiGet<Record<string, unknown>[]>(
      `/api/model-effectiveness/full-universe/head-bands?factor_version=${QUANT_FACTOR_VERSION}` +
        `&evaluation_version=${FULL_UNIVERSE_EVALUATION_VERSION}&horizon=${horizon}`,
    ),
  fullUniverseFactorIc: (horizon = 3) =>
    apiGet<Record<string, unknown>[]>(
      `/api/model-effectiveness/full-universe/factor-ic?factor_version=${QUANT_FACTOR_VERSION}` +
        `&evaluation_version=${FULL_UNIVERSE_EVALUATION_VERSION}&horizon=${horizon}`,
    ),
};
