/// <reference types="vite/client" />

interface Window {
  aiTraderShell?: {
    getConnection: () => Promise<{ baseUrl: string; sessionToken: string; appVersion: string }>;
    getRuntimeStatus: () => Promise<{ appVersion: string; firstRun: { firstRun: boolean; seedVersion: string | null }; userData: string }>;
    restartBackend: () => Promise<{ baseUrl: string; sessionToken: string; appVersion: string }>;
    openLogs: () => Promise<string>;
    notifyMonitorAlert: (payload: { title: string; body: string; severity: string; stockCode: string }) => Promise<{ shown: boolean }>;
    onOpenMonitorStock: (callback: (stockCode: string) => void) => () => void;
    secrets: {
      status: () => Promise<Record<string, { configured: boolean }>>;
      set: (provider: string, value: string) => Promise<void>;
      delete: (provider: string) => Promise<void>;
      test: (provider: string) => Promise<unknown>;
    };
  };
}
