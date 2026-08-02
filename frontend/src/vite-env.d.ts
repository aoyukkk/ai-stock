/// <reference types="vite/client" />

interface Window {
  aiTraderShell?: {
    backend: {
      request: (request: {
        method: string;
        url: string;
        data?: unknown;
        params?: Record<string, string | number | boolean | Array<string | number | boolean> | null>;
      }) => Promise<{ status: number; data: unknown }>;
    };
    getRuntimeStatus: () => Promise<{ appVersion: string; firstRun: { firstRun: boolean; seedVersion: string | null }; userData: string }>;
    restartBackend: () => Promise<{ restarted: boolean }>;
    openLogs: () => Promise<string>;
    openExternal: (url: string) => Promise<{ opened: boolean }>;
    notifyMonitorAlert: (payload: { title: string; body: string; severity: string; stockCode: string }) => Promise<{ shown: boolean }>;
    onOpenMonitorStock: (callback: (stockCode: string) => void) => () => void;
    secrets: {
      status: () => Promise<Record<string, {
        configured: boolean;
        provider: string;
        storage_backend: "electron_safe_storage";
        updated_at: string | null;
        validation_status: "ENCRYPTED_AT_REST" | "NOT_CONFIGURED" | "OS_ENCRYPTION_UNAVAILABLE";
      }>>;
      set: (provider: string, value: string) => Promise<void>;
      delete: (provider: string) => Promise<void>;
      test: (provider: string) => Promise<unknown>;
    };
  };
}
