export interface ApiEnvelope<T = unknown> {
  success: boolean;
  code?: string;
  message?: string;
  data: T;
  trace_id?: string;
  error?: { code: string; message: string; details?: Record<string, unknown> } | null;
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
