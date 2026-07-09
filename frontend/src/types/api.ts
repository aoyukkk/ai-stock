export interface ApiEnvelope<T = unknown> {
  success: boolean;
  code: string;
  message: string;
  data: T;
  trace_id?: string;
}

export interface FrontendApiError {
  success: false;
  code: string;
  message: string;
  traceId?: string;
  status?: number;
  data?: unknown;
}

export type ApiData = Record<string, unknown>;
