import { defineStore } from "pinia";

import {
  getConfigHistory,
  getEditableConfig,
  getEffectiveConfig,
  resetConfigValue,
  updateConfigBulk
} from "@/api/config";
import type {
  ConfigHistoryItem,
  ConfigValue,
  EditableConfigItem,
  EffectiveConfig
} from "@/types/config";

interface ConfigState {
  effective: EffectiveConfig | null;
  editable: EditableConfigItem[];
  drafts: Record<string, ConfigValue>;
  originals: Record<string, ConfigValue>;
  loading: boolean;
  saving: boolean;
  dirty: boolean;
  lastError: string;
  lastTraceId: string;
  history: ConfigHistoryItem[];
}

export const categoryLabels: Record<string, string> = {
  stock_scan: "Stock Scan",
  refresh_frequency: "Refresh Frequency",
  quant_weights: "Quant Weights",
  risk_alert: "Risk Alerts",
  order_price: "Order Price",
  virtual_trading: "Virtual Trading",
  memory: "Memory",
  llm: "LLM Mock Config"
};

export const categoryOrder = [
  "stock_scan",
  "refresh_frequency",
  "quant_weights",
  "risk_alert",
  "order_price",
  "virtual_trading",
  "memory",
  "llm"
];

export const useConfigStore = defineStore("config", {
  state: (): ConfigState => ({
    effective: null,
    editable: [],
    drafts: {},
    originals: {},
    loading: false,
    saving: false,
    dirty: false,
    lastError: "",
    lastTraceId: "",
    history: []
  }),
  getters: {
    groupedItems(state): Record<string, EditableConfigItem[]> {
      return state.editable.reduce<Record<string, EditableConfigItem[]>>((groups, item) => {
        groups[item.category] = groups[item.category] || [];
        groups[item.category].push(item);
        return groups;
      }, {});
    },
    realTradingEnabled(state): boolean {
      return Boolean(state.effective?.real_trading_enabled);
    }
  },
  actions: {
    async loadConfig() {
      this.loading = true;
      this.lastError = "";
      try {
        const [editableResponse, effectiveResponse] = await Promise.all([
          getEditableConfig(),
          getEffectiveConfig()
        ]);
        this.editable = editableResponse.data.items;
        this.effective = effectiveResponse.data;
        this.lastTraceId = effectiveResponse.trace_id || editableResponse.trace_id || "";
        this.resetDraftsFromEditable();
      } catch (error) {
        this.captureError(error);
        throw error;
      } finally {
        this.loading = false;
      }
    },
    async loadHistory(configKey?: string) {
      const response = await getConfigHistory({ config_key: configKey, limit: 100 });
      this.history = response.data.items;
      this.lastTraceId = response.trace_id || this.lastTraceId;
    },
    updateDraft(configKey: string, value: ConfigValue) {
      this.drafts[configKey] = value;
      this.dirty = this.hasDirtyDrafts();
    },
    itemsForCategory(category: string): EditableConfigItem[] {
      return this.groupedItems[category] || [];
    },
    labelForCategory(category: string): string {
      return categoryLabels[category] || category;
    },
    draftValue(configKey: string): ConfigValue {
      return this.drafts[configKey] ?? null;
    },
    originalValue(configKey: string): ConfigValue {
      return this.originals[configKey] ?? null;
    },
    isDirty(configKey: string): boolean {
      return JSON.stringify(this.drafts[configKey]) !== JSON.stringify(this.originals[configKey]);
    },
    dirtyItemsForCategory(category: string) {
      return this.itemsForCategory(category)
        .filter((item) => this.isDirty(item.config_key))
        .map((item) => ({
          config_key: item.config_key,
          value: this.drafts[item.config_key]
        }));
    },
    categoryHasDirtyDrafts(category: string): boolean {
      return this.dirtyItemsForCategory(category).length > 0;
    },
    quantWeightSum(): number {
      return this.itemsForCategory("quant_weights").reduce((sum, item) => {
        return sum + Number(this.drafts[item.config_key] ?? 0);
      }, 0);
    },
    validateCategory(category: string): string {
      if (category !== "quant_weights") {
        return "";
      }
      const total = this.quantWeightSum();
      return Math.abs(total - 1) <= 0.0001 ? "" : `Quant weights must sum to 1. Current sum: ${total.toFixed(4)}`;
    },
    async saveCategory(category: string, reason: string) {
      const validationError = this.validateCategory(category);
      if (validationError) {
        throw new Error(validationError);
      }

      const items = this.dirtyItemsForCategory(category);
      if (!items.length) {
        return { items: [] };
      }

      this.saving = true;
      try {
        const response = await updateConfigBulk(
          items,
          "local_admin",
          reason || `frontend update: ${category}`
        );
        this.lastTraceId = response.trace_id || this.lastTraceId;
        await this.loadConfig();
        await this.loadHistory();
        return response.data;
      } catch (error) {
        this.captureError(error);
        throw error;
      } finally {
        this.saving = false;
      }
    },
    async resetItem(configKey: string, reason = "frontend reset") {
      this.saving = true;
      try {
        const response = await resetConfigValue(configKey, "local_admin", reason);
        this.lastTraceId = response.trace_id || this.lastTraceId;
        await this.loadConfig();
        await this.loadHistory(configKey);
        return response.data;
      } catch (error) {
        this.captureError(error);
        throw error;
      } finally {
        this.saving = false;
      }
    },
    resetDraftsFromEditable() {
      const values = this.effective?.values || {};
      const nextDrafts: Record<string, ConfigValue> = {};
      const nextOriginals: Record<string, ConfigValue> = {};
      this.editable.forEach((item) => {
        const value = values[item.config_key] ?? item.current_value;
        nextDrafts[item.config_key] = value;
        nextOriginals[item.config_key] = value;
      });
      this.drafts = nextDrafts;
      this.originals = nextOriginals;
      this.dirty = false;
    },
    hasDirtyDrafts(): boolean {
      return Object.keys(this.drafts).some((key) => this.isDirty(key));
    },
    captureError(error: unknown) {
      const apiError = error as { message?: string; code?: string; traceId?: string };
      const code = apiError.code ? `${apiError.code}: ` : "";
      const trace = apiError.traceId ? ` trace_id=${apiError.traceId}` : "";
      this.lastError = `${code}${apiError.message || "Config request failed"}${trace}`;
      this.lastTraceId = apiError.traceId || this.lastTraceId;
    }
  }
});
