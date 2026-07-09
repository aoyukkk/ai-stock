import axios, { type AxiosError, type AxiosRequestConfig } from "axios";

import type { ApiEnvelope, FrontendApiError } from "@/types/api";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";
const sensitiveKeys = ["api_key", "password", "secret", "token", "username", "credential"];

export const http = axios.create({
  baseURL: apiBaseUrl,
  timeout: 20000
});

export function createTraceId(): string {
  const random = Math.random().toString(16).slice(2);
  return `frontend-${Date.now()}-${random}`;
}

http.interceptors.request.use((config) => {
  config.headers = config.headers || {};
  if (!config.headers["X-Trace-Id"]) {
    config.headers["X-Trace-Id"] = createTraceId();
  }
  return config;
});

export async function apiGet<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<ApiEnvelope<T>> {
  return request<T>({ ...config, method: "GET", url });
}

export async function apiPost<T = unknown>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig
): Promise<ApiEnvelope<T>> {
  return request<T>({ ...config, method: "POST", url, data });
}

export async function apiPut<T = unknown>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig
): Promise<ApiEnvelope<T>> {
  return request<T>({ ...config, method: "PUT", url, data });
}

async function request<T>(config: AxiosRequestConfig): Promise<ApiEnvelope<T>> {
  try {
    const response = await http.request<ApiEnvelope<T>>(config);
    return sanitizeEnvelope(response.data);
  } catch (error) {
    throw normalizeError(error);
  }
}

export function sanitizeEnvelope<T>(envelope: ApiEnvelope<T>): ApiEnvelope<T> {
  return {
    ...envelope,
    data: sanitizeSensitive(envelope.data) as T
  };
}

export function sanitizeSensitive(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeSensitive(item));
  }
  if (value && typeof value === "object") {
    const result: Record<string, unknown> = {};
    Object.entries(value as Record<string, unknown>).forEach(([key, item]) => {
      result[key] = isSensitiveKey(key) ? "[REDACTED]" : sanitizeSensitive(item);
    });
    return result;
  }
  return value;
}

function isSensitiveKey(key: string): boolean {
  const normalized = key.toLowerCase();
  if (normalized.endsWith("_tokens") || normalized.endsWith("_token_budget")) {
    return false;
  }
  return sensitiveKeys.some((item) => normalized.includes(item));
}

function normalizeError(error: unknown): FrontendApiError {
  const axiosError = error as AxiosError<ApiEnvelope>;
  const envelope = axiosError.response?.data;
  if (envelope) {
    return {
      success: false,
      code: envelope.code || "API_ERROR",
      message: envelope.message || "API request failed",
      traceId: envelope.trace_id,
      status: axiosError.response?.status,
      data: sanitizeSensitive(envelope.data)
    };
  }
  return {
    success: false,
    code: "NETWORK_ERROR",
    message: axiosError.message || "Network request failed",
    status: axiosError.response?.status
  };
}
