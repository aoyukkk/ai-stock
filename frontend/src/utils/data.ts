export type Row = Record<string, unknown>;

export function rowsFrom(data: unknown, key: string): Row[] {
  if (!data || typeof data !== "object") {
    return [];
  }
  const value = (data as Row)[key];
  return Array.isArray(value) ? (value as Row[]) : [];
}

export function valueFrom<T = unknown>(data: unknown, key: string, fallback: T): T {
  if (!data || typeof data !== "object") {
    return fallback;
  }
  const value = (data as Row)[key];
  return (value ?? fallback) as T;
}

export function nestedValue<T = unknown>(data: unknown, path: string[], fallback: T): T {
  let current = data;
  for (const key of path) {
    if (!current || typeof current !== "object") {
      return fallback;
    }
    current = (current as Row)[key];
  }
  return (current ?? fallback) as T;
}
