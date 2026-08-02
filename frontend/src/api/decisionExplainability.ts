import { apiGet } from "@/api/http";
import type { ApiEnvelope } from "@/types/api";
import type {
  DecisionExplainabilityDetail,
  DecisionExplainabilityRow,
  DecisionExplainabilitySummary,
  GateEvaluationRow,
  FactorPerformanceRow,
  GateValueRow,
} from "@/types/decisionExplainability";

export function getLatestDecisionExplainability(
  tradeDate: string,
): Promise<ApiEnvelope<DecisionExplainabilitySummary | null>> {
  return apiGet("/api/workbench/decision-explainability/latest", { params: { trade_date: tradeDate } });
}

export function getDecisionExplainabilityResults(
  runId: string,
  page: number,
  pageSize: number,
  admissionState?: string,
  strategyStatus?: string,
): Promise<ApiEnvelope<{ items: DecisionExplainabilityRow[]; total: number; page: number; page_size: number }>> {
  return apiGet("/api/workbench/decision-explainability/results", {
    params: {
      run_id: runId,
      page,
      page_size: pageSize,
      ...(admissionState ? { admission_state: admissionState } : {}),
      ...(strategyStatus ? { strategy_status: strategyStatus } : {}),
    },
  });
}

export function getDecisionExplainabilityDetail(
  runId: string,
  stockCode: string,
): Promise<ApiEnvelope<DecisionExplainabilityDetail>> {
  return apiGet("/api/workbench/decision-explainability/detail", {
    params: { run_id: runId, stock_code: stockCode },
  });
}

export function getGateEvaluations(runId: string): Promise<ApiEnvelope<GateEvaluationRow[]>> {
  return apiGet("/api/workbench/decision-explainability/gates", { params: { run_id: runId } });
}

export function getLatestFactorPerformance(endDate: string): Promise<ApiEnvelope<FactorPerformanceRow[]>> {
  return apiGet("/api/workbench/decision-explainability/factor-performance/latest", {
    params: { end_date: endDate },
  });
}

export function getGateValueRanking(endDate: string, periodDays = 60): Promise<ApiEnvelope<GateValueRow[]>> {
  return apiGet("/api/workbench/decision-explainability/gate-ranking", {
    params: { end_date: endDate, period_days: periodDays },
  });
}
