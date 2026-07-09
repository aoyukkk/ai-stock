import { defineStore } from "pinia";

import { getDataSourceStatus } from "@/api/dataSources";
import { getLLMStatus } from "@/api/llm";
import { getConfigSummary, getDatabaseHealth, getHealth } from "@/api/system";

export const useAppStore = defineStore("app", {
  state: () => ({
    appName: import.meta.env.VITE_APP_NAME || "AI Trader Assistant",
    appVersion: "0.3.0",
    apiBaseUrl: import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000",
    apiConnected: false,
    loading: false,
    lastError: "",
    currentTraceId: "",
    health: {} as Record<string, unknown>,
    configSummary: {} as Record<string, unknown>,
    databaseHealth: {} as Record<string, unknown>,
    dataSourceStatus: {} as Record<string, unknown>,
    llmStatus: {} as Record<string, unknown>
  }),
  getters: {
    realTradingEnabled(state): boolean {
      const system = getNested<Record<string, unknown>>(state.configSummary, ["system"]) || {};
      return Boolean(system.real_trading_enabled);
    },
    llmMode(state): string {
      const llm = getNested<Record<string, unknown>>(state.configSummary, ["llm"]) || {};
      const statusMode = String(state.llmStatus.mock_only === true ? "mock_only" : "");
      return statusMode || String(llm.mode || "unknown");
    },
    dataSourceMode(state): string {
      const dataSources = getNested<Record<string, unknown>>(state.configSummary, ["data_sources"]) || {};
      return String(dataSources.mode || "unknown");
    },
    mockDataEnabled(state): boolean {
      const dataSources = getNested<Record<string, unknown>>(state.configSummary, ["data_sources"]) || {};
      return Boolean(dataSources.mock_enabled);
    },
    mockLlmEnabled(state): boolean {
      const llm = getNested<Record<string, unknown>>(state.configSummary, ["llm"]) || {};
      return Boolean(llm.mock_enabled) || state.llmStatus.mock_only === true;
    }
  },
  actions: {
    async refreshStatus() {
      this.loading = true;
      this.lastError = "";
      try {
        const [health, config, database, dataSources, llm] = await Promise.all([
          getHealth(),
          getConfigSummary(),
          getDatabaseHealth(),
          getDataSourceStatus(),
          getLLMStatus()
        ]);
        this.health = health.data;
        this.configSummary = config.data;
        this.databaseHealth = database.data;
        this.dataSourceStatus = dataSources.data;
        this.llmStatus = llm.data;
        this.currentTraceId = llm.trace_id || dataSources.trace_id || config.trace_id || health.trace_id || "";
        this.apiConnected = true;
      } catch (error) {
        const apiError = error as { message?: string; code?: string; traceId?: string };
        this.apiConnected = false;
        const code = apiError.code ? `${apiError.code}: ` : "";
        const trace = apiError.traceId ? ` trace_id=${apiError.traceId}` : "";
        this.lastError = `${code}${apiError.message || "Unable to connect to backend"}${trace}`;
        this.currentTraceId = apiError.traceId || this.currentTraceId;
      } finally {
        this.loading = false;
      }
    }
  }
});

function getNested<T>(obj: Record<string, unknown>, path: string[]): T | undefined {
  let current: unknown = obj;
  for (const key of path) {
    if (!current || typeof current !== "object") {
      return undefined;
    }
    current = (current as Record<string, unknown>)[key];
  }
  return current as T;
}
