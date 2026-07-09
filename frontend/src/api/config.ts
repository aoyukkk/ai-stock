import type {
  ConfigHistoryItem,
  ConfigUpdateResult,
  ConfigValue,
  EditableConfigItem,
  EffectiveConfig
} from "@/types/config";

import { apiGet, apiPost, apiPut } from "./http";

export function getEffectiveConfig() {
  return apiGet<EffectiveConfig>("/api/v1/config/effective");
}

export function getEditableConfig() {
  return apiGet<{ items: EditableConfigItem[] }>("/api/v1/config/editable");
}

export function updateConfigValue(
  configKey: string,
  value: ConfigValue,
  user = "local_admin",
  reason = "frontend update"
) {
  return apiPut<ConfigUpdateResult>(`/api/v1/config/values/${encodeURIComponent(configKey)}`, {
    value,
    user,
    reason
  });
}

export function updateConfigBulk(
  items: Array<{ config_key: string; value: ConfigValue }>,
  user = "local_admin",
  reason = "bulk update from frontend"
) {
  return apiPost<{ items: ConfigUpdateResult[] }>("/api/v1/config/bulk", {
    items,
    user,
    reason
  });
}

export function resetConfigValue(
  configKey: string,
  user = "local_admin",
  reason = "frontend reset"
) {
  return apiPost<ConfigUpdateResult>(`/api/v1/config/values/${encodeURIComponent(configKey)}/reset`, {
    user,
    reason
  });
}

export function getConfigHistory(params?: { config_key?: string; limit?: number }) {
  return apiGet<{ items: ConfigHistoryItem[] }>("/api/v1/config/history", { params });
}
