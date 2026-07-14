/// <reference types="vite/client" />

interface Window {
  aiTraderShell?: {
    getConnection: () => Promise<{ baseUrl: string; sessionToken: string; appVersion: string }>;
    getRuntimeStatus: () => Promise<{ appVersion: string; firstRun: { firstRun: boolean; seedVersion: string | null }; userData: string }>;
    restartBackend: () => Promise<{ baseUrl: string; sessionToken: string; appVersion: string }>;
    openLogs: () => Promise<string>;
    secrets: {
      status: () => Promise<Record<string, { configured: boolean }>>;
      set: (provider: string, value: string) => Promise<void>;
      delete: (provider: string) => Promise<void>;
      test: (provider: string) => Promise<unknown>;
    };
  };
}
