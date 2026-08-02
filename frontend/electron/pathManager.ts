import { app } from "electron";
import path from "node:path";

export interface DesktopPaths {
  root: string;
  data: string;
  database: string;
  cache: string;
  outputs: string;
  logs: string;
  backups: string;
  config: string;
  secrets: string;
  diagnostics: string;
  temp: string;
}

export function desktopPaths(): DesktopPaths {
  const root = app.getPath("userData");
  return {
    root,
    data: path.join(root, "data"),
    database: path.join(root, "data", "ai_trader.db"),
    cache: path.join(root, "cache"),
    outputs: path.join(root, "outputs"),
    logs: path.join(root, "logs"),
    backups: path.join(root, "backups"),
    config: path.join(root, "config"),
    secrets: path.join(root, "secrets"),
    diagnostics: path.join(root, "diagnostics"),
    temp: path.join(root, "temp")
  };
}

const SENSITIVE_ENV_NAME = /(TOKEN|API[_-]?KEY|PASSWORD|PASSWD|SECRET|CREDENTIAL|AUTHORIZATION|COOKIE)/i;

export function backendEnvironment(
  paths: DesktopPaths,
  port: number,
  token: string,
  allowLegacySecretFallback = false
): NodeJS.ProcessEnv {
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(([name]) => allowLegacySecretFallback || !SENSITIVE_ENV_NAME.test(name))
  );
  return {
    ...inherited,
    AI_TRADER_USER_DATA_DIR: paths.root,
    AI_TRADER_DB_PATH: paths.database,
    AI_TRADER_CACHE_DIR: paths.cache,
    AI_TRADER_OUTPUT_DIR: paths.outputs,
    AI_TRADER_LOG_DIR: paths.logs,
    AI_TRADER_BACKUP_DIR: paths.backups,
    AI_TRADER_CONFIG_DIR: paths.config,
    AI_TRADER_DIAGNOSTICS_DIR: paths.diagnostics,
    AI_TRADER_ENV: "production",
    APP_ENV: "production",
    AI_TRADER_DESKTOP_MODE: "true",
    AI_TRADER_PORT: String(port),
    AI_TRADER_LOCAL_API_TOKEN: token,
    PYTHON_DOTENV_DISABLED: allowLegacySecretFallback ? "0" : "1",
    AI_TRADER_LEGACY_SECRET_FALLBACK: allowLegacySecretFallback ? "true" : "false",
    ENABLE_REAL_TRADING: "false",
    SCHEDULER_ENABLED: "false",
    PAPER_TRADING_ENABLED: "false",
    AUTO_ORDER_CREATION: "false",
    LLM_REAL_CALLS_ENABLED: "false",
    RUN_REAL_FUNDAMENTAL_RESEARCH: "false"
  };
}
