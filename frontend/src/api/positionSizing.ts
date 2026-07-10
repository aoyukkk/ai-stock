import { apiPost } from "./http";

export interface PositionSuggestion {
  stock_code: string;
  relative_allocation_weight: string;
  account_position_percent: string;
  suggested_capital: string;
  suggested_quantity: number;
  maximum_planned_loss: string;
  binding_constraint: string;
  warnings: string[];
}

export function evaluatePositionSizing(payload: unknown) {
  return apiPost("/api/v1/position-sizing/evaluate", payload);
}
