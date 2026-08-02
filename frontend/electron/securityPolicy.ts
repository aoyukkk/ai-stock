import path from "node:path";
import { fileURLToPath } from "node:url";

export const DEVELOPMENT_RENDERER_URL = `http://${["127", "0", "0", "1"].join(".")}:${5173}/`;
export const SECRET_PROVIDERS = [
  "tushare",
  "deepseek",
  "openai",
  "tavily",
  "ifind_username",
  "ifind_password",
  "ifind_access",
  "ifind_refresh"
] as const;
export type SecretProvider = (typeof SECRET_PROVIDERS)[number];

export interface BackendProxyRequest {
  method: string;
  url: string;
  data?: unknown;
  params?: Record<string, string | number | boolean | Array<string | number | boolean> | null>;
}

const ALLOWED_METHODS = new Set(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]);
const ALLOWED_API_PREFIXES = ["/api/", "/health"];
const DENIED_BACKEND_PATHS = ["/api/runtime/secrets", "/api/runtime/shutdown", "/api/workbench/secrets"];
const MAX_URL_LENGTH = 2048;
const MAX_SECRET_BYTES = 8192;
const MAX_BODY_BYTES = 1024 * 1024;

export function isAllowedRendererUrl(candidate: string, packagedEntry: string, packaged: boolean): boolean {
  try {
    const parsed = new URL(candidate);
    if (parsed.username || parsed.password || parsed.search) return false;
    if (!packaged) {
      const expected = new URL(DEVELOPMENT_RENDERER_URL);
      return parsed.protocol === expected.protocol
        && parsed.hostname === expected.hostname
        && parsed.port === expected.port
        && parsed.pathname === "/";
    }
    if (parsed.protocol !== "file:") return false;
    return normalizedFilePath(candidate) === normalizedFilePath(packagedEntry);
  } catch {
    return false;
  }
}

export function isAllowedExternalUrl(candidate: unknown): candidate is string {
  if (typeof candidate !== "string" || candidate.length === 0 || candidate.length > MAX_URL_LENGTH) return false;
  if (candidate.trim() !== candidate || /[\u0000-\u001f\u007f]/.test(candidate)) return false;
  try {
    const parsed = new URL(candidate);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

export function requireProvider(candidate: unknown): SecretProvider {
  const normalized = typeof candidate === "string" ? candidate.trim().toLowerCase() : "";
  if (SECRET_PROVIDERS.includes(normalized as SecretProvider)) {
    return normalized as SecretProvider;
  }
  throw new Error("INVALID_SECRET_PROVIDER");
}

export function requireSecretValue(candidate: unknown): string {
  if (typeof candidate !== "string" || candidate.length === 0 || candidate.includes("\0")) {
    throw new Error("INVALID_SECRET_VALUE");
  }
  if (Buffer.byteLength(candidate, "utf8") > MAX_SECRET_BYTES) throw new Error("SECRET_VALUE_TOO_LARGE");
  return candidate;
}

export function requireBackendProxyRequest(candidate: unknown): BackendProxyRequest {
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) throw new Error("INVALID_BACKEND_REQUEST");
  const raw = candidate as Record<string, unknown>;
  const allowedKeys = new Set(["method", "url", "data", "params"]);
  if (Object.keys(raw).some((key) => !allowedKeys.has(key))) throw new Error("INVALID_BACKEND_REQUEST_FIELD");

  const method = String(raw.method || "GET").toUpperCase();
  if (!ALLOWED_METHODS.has(method)) throw new Error("INVALID_BACKEND_METHOD");
  if (typeof raw.url !== "string" || raw.url.length === 0 || raw.url.length > MAX_URL_LENGTH) {
    throw new Error("INVALID_BACKEND_URL");
  }
  if (raw.url.includes("\\") || raw.url.includes("://") || raw.url.startsWith("//")) throw new Error("INVALID_BACKEND_URL");
  const parsed = new URL(raw.url, "http://127.0.0.1");
  if (parsed.origin !== "http://127.0.0.1" || !ALLOWED_API_PREFIXES.some((prefix) => parsed.pathname === prefix || parsed.pathname.startsWith(prefix))) {
    throw new Error("BACKEND_PATH_NOT_ALLOWED");
  }
  if (DENIED_BACKEND_PATHS.some((denied) => parsed.pathname === denied || parsed.pathname.startsWith(`${denied}/`))) {
    throw new Error("BACKEND_PATH_NOT_ALLOWED");
  }
  if (raw.data !== undefined && serializedBytes(raw.data) > MAX_BODY_BYTES) throw new Error("BACKEND_BODY_TOO_LARGE");
  const params = validateParams(raw.params);
  return { method, url: `${parsed.pathname}${parsed.search}`, data: raw.data, ...(params ? { params } : {}) };
}

export function isTrustedSenderContext(input: {
  senderId: number;
  expectedSenderId: number;
  isMainFrame: boolean;
  senderUrl: string;
  packagedEntry: string;
  packaged: boolean;
}): boolean {
  return input.senderId === input.expectedSenderId
    && input.isMainFrame
    && isAllowedRendererUrl(input.senderUrl, input.packagedEntry, input.packaged);
}

function normalizedFilePath(value: string): string {
  const parsed = new URL(value);
  parsed.hash = "";
  return path.resolve(fileURLToPath(parsed)).toLocaleLowerCase("en-US");
}

function serializedBytes(value: unknown): number {
  let serialized: string | undefined;
  try {
    serialized = JSON.stringify(value);
  } catch {
    throw new Error("BACKEND_BODY_NOT_SERIALIZABLE");
  }
  if (serialized === undefined) throw new Error("BACKEND_BODY_NOT_SERIALIZABLE");
  return Buffer.byteLength(serialized, "utf8");
}

function validateParams(value: unknown): BackendProxyRequest["params"] | undefined {
  if (value === undefined) return undefined;
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("INVALID_BACKEND_PARAMS");
  const result: NonNullable<BackendProxyRequest["params"]> = {};
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (!/^[A-Za-z0-9_.-]{1,80}$/.test(key)) throw new Error("INVALID_BACKEND_PARAM_NAME");
    if (Array.isArray(item)) {
      if (item.length > 100 || item.some((entry) => !isScalar(entry))) throw new Error("INVALID_BACKEND_PARAM_VALUE");
      result[key] = item as Array<string | number | boolean>;
    } else if (item === null || isScalar(item)) {
      result[key] = item;
    } else {
      throw new Error("INVALID_BACKEND_PARAM_VALUE");
    }
  }
  return result;
}

function isScalar(value: unknown): value is string | number | boolean {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}
