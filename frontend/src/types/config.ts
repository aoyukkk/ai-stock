export interface SafetySummary {
  realTradingEnabled: boolean;
  llmMode: string;
  dataSourceMode: string;
  mockDataEnabled: boolean;
  mockLlmEnabled: boolean;
}

export type ConfigValue = string | number | boolean | null;

export interface EditableConfigItem {
  config_key: string;
  current_value: ConfigValue;
  value_type: "integer" | "number" | "boolean" | "string";
  category: string;
  description: string;
  editable: boolean;
  constraints: Record<string, unknown>;
  source: string;
}

export interface EffectiveConfig {
  priority: string[];
  real_trading_enabled: boolean;
  llm_mock_only: boolean;
  data_source_mode: string;
  values: Record<string, ConfigValue>;
  sources: Record<string, string>;
  groups: Array<{ category: string; items: EditableConfigItem[] }>;
}

export interface ConfigHistoryItem {
  id: number;
  config_key: string;
  old_value: ConfigValue;
  new_value: ConfigValue;
  user: string;
  reason: string;
  time: string;
}

export interface ConfigUpdateResult {
  config_key: string;
  old_value: ConfigValue;
  new_value: ConfigValue;
  effective_value: ConfigValue;
}
