import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Notification,
  session,
  shell,
  type IpcMainInvokeEvent,
  type WebContents
} from "electron";
import { appendFile, mkdir, realpath } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { BackendManager } from "./backendManager.js";
import { initializeFirstRun, type FirstRunResult } from "./firstRunManager.js";
import { desktopPaths } from "./pathManager.js";
import { SecretManager } from "./secretManager.js";
import {
  DEVELOPMENT_RENDERER_URL,
  isAllowedExternalUrl,
  isAllowedRendererUrl,
  isTrustedSenderContext,
  requireBackendProxyRequest,
  requireProvider,
  requireSecretValue
} from "./securityPolicy.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
if (process.env.AI_TRADER_SMOKE_TEST === "true" && process.env.AI_TRADER_SMOKE_USER_DATA_DIR) {
  app.setPath("userData", path.resolve(process.env.AI_TRADER_SMOKE_USER_DATA_DIR));
}
const hasLock = app.requestSingleInstanceLock();
let mainWindow: BrowserWindow | null = null;
let backend: BackendManager | null = null;
let firstRun: FirstRunResult = { firstRun: false, seedVersion: null };
let quitting = false;

function packagedRendererEntry(): string {
  return path.join(__dirname, "../../dist/index.html");
}

function packagedRendererUrl(): string {
  return pathToFileURL(packagedRendererEntry()).toString();
}

if (!hasLock) app.quit();

app.on("second-instance", () => {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
});

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 760,
    title: "AI Trader Assistant",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      devTools: !app.isPackaged
    }
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  if (!app.isPackaged) {
    await mainWindow.loadURL(DEVELOPMENT_RENDERER_URL);
  } else {
    await mainWindow.loadFile(packagedRendererEntry(), { hash: firstRun.firstRun ? "/first-run" : "/workbench" });
  }
}

async function bootstrap(): Promise<void> {
  const paths = desktopPaths();
  await mkdir(paths.logs, { recursive: true });
  installSessionGuards();
  const secrets = new SecretManager(paths.secrets);
  backend = new BackendManager(paths, secrets);
  registerIpc(paths, secrets);
  try {
    firstRun = await initializeFirstRun(paths);
    await backend.start();
    if (process.env.AI_TRADER_SMOKE_TEST === "true") {
      await appendFile(path.join(paths.logs, "electron.log"), `${new Date().toISOString()} packaged smoke test passed\n`, "utf8");
      await backend.stop();
      quitting = true;
      app.quit();
      return;
    }
    await createWindow();
  } catch (error) {
    const message = error instanceof Error ? error.message : "UNKNOWN_STARTUP_ERROR";
    await appendFile(path.join(paths.logs, "electron.log"), `${new Date().toISOString()} startup failed: ${message}\n`, "utf8");
    if (process.env.AI_TRADER_SMOKE_TEST === "true") {
      quitting = true;
      app.exit(1);
      return;
    }
    const choice = await dialog.showMessageBox({
      type: "error",
      title: "AI Trader Assistant 启动失败",
      message: "桌面后端未能启动。",
      detail: `错误：${message}\n日志：${paths.logs}`,
      buttons: ["打开日志目录", "退出"],
      defaultId: 0,
      cancelId: 1
    });
    if (choice.response === 0) await shell.openPath(paths.logs);
    app.quit();
  }
}

function registerIpc(paths: ReturnType<typeof desktopPaths>, secrets: SecretManager): void {
  ipcMain.handle("backend:request", trustedHandler((candidate) => {
    if (!backend) throw new Error("BACKEND_NOT_READY");
    return backend.request(requireBackendProxyRequest(candidate));
  }));
  ipcMain.handle("runtime:status", trustedHandler(() => ({
    appVersion: app.getVersion(),
    firstRun,
    userData: paths.root
  })));
  ipcMain.handle("runtime:restart-backend", trustedHandler(async () => {
    if (!backend) throw new Error("BACKEND_NOT_READY");
    await backend.restart();
    return { restarted: true };
  }));
  ipcMain.handle("runtime:open-logs", trustedHandler(async () => {
    const root = await realpath(paths.root);
    const logs = await realpath(paths.logs);
    if (!isPathWithin(logs, root)) throw new Error("LOG_PATH_OUTSIDE_USER_DATA");
    return shell.openPath(logs);
  }));
  ipcMain.handle("runtime:open-external", trustedHandler(async (candidate) => {
    if (!isAllowedExternalUrl(candidate)) throw new Error("EXTERNAL_URL_NOT_ALLOWED");
    await shell.openExternal(candidate);
    return { opened: true };
  }));
  ipcMain.handle("monitor:notify", trustedHandler((candidate) => {
    const payload = requireNotificationPayload(candidate);
    if (!Notification.isSupported()) return { shown: false };
    const notification = new Notification({
      title: payload.title || "实时盯盘提醒",
      body: payload.body || "请打开提醒中心人工复核。",
      silent: payload.severity !== "CRITICAL"
    });
    notification.on("click", () => {
      if (!mainWindow) return;
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
      mainWindow.webContents.send("monitor:open-stock", payload.stockCode);
    });
    notification.show();
    return { shown: true };
  }));
  ipcMain.handle("secrets:status", trustedHandler(() => secrets.status()));
  ipcMain.handle("secrets:set", trustedHandler((provider, value) => {
    if (!backend) throw new Error("BACKEND_NOT_READY");
    return backend.saveSecret(requireProvider(provider), requireSecretValue(value));
  }));
  ipcMain.handle("secrets:delete", trustedHandler((provider) => {
    if (!backend) throw new Error("BACKEND_NOT_READY");
    return backend.deleteSecret(requireProvider(provider));
  }));
  ipcMain.handle("secrets:test", trustedHandler(async (provider) => {
    if (!backend) throw new Error("BACKEND_NOT_READY");
    const checkedProvider = requireProvider(provider);
    const connection = backend.currentConnection();
    const response = await fetch(`${connection.baseUrl}/api/workbench/secrets/${checkedProvider}/test`, {
      method: "POST",
      headers: { "X-AI-Trader-Token": connection.sessionToken },
      redirect: "manual",
      signal: AbortSignal.timeout(20000)
    });
    if (!response.headers.get("content-type")?.includes("application/json")) {
      throw new Error("INVALID_BACKEND_RESPONSE");
    }
    return response.json();
  }));
}

function trustedHandler(handler: (...args: unknown[]) => unknown): (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown {
  return (event, ...args) => {
    assertTrustedSender(event);
    return handler(...args);
  };
}

function assertTrustedSender(event: IpcMainInvokeEvent): void {
  const expected = mainWindow?.webContents;
  const frame = event.senderFrame;
  const trusted = Boolean(expected && frame && isTrustedSenderContext({
    senderId: event.sender.id,
    expectedSenderId: expected.id,
    isMainFrame: frame === event.sender.mainFrame,
    senderUrl: frame.url,
    packagedEntry: packagedRendererUrl(),
    packaged: app.isPackaged
  }));
  if (!trusted) throw new Error("IPC_UNTRUSTED_SENDER");
}

function installWebContentsGuards(contents: WebContents): void {
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-navigate", (event, url) => {
    if (!isAllowedRendererUrl(url, packagedRendererUrl(), app.isPackaged)) event.preventDefault();
  });
}

function installSessionGuards(): void {
  session.defaultSession.setPermissionCheckHandler(() => false);
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  session.defaultSession.on("will-download", (event) => event.preventDefault());
  app.on("web-contents-created", (_event, contents) => installWebContentsGuards(contents));
}

function isPathWithin(candidate: string, parent: string): boolean {
  const relative = path.relative(parent, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function requireNotificationPayload(candidate: unknown): {
  title: string;
  body: string;
  severity: string;
  stockCode: string;
} {
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) throw new Error("INVALID_NOTIFICATION_PAYLOAD");
  const value = candidate as Record<string, unknown>;
  const clean = (item: unknown, max: number) => String(item || "").replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, max);
  return {
    title: clean(value.title, 120),
    body: clean(value.body, 300),
    severity: clean(value.severity, 20),
    stockCode: clean(value.stockCode, 32).replace(/[^A-Za-z0-9._-]/g, "")
  };
}

void app.whenReady().then(bootstrap);

app.on("before-quit", (event) => {
  if (quitting || !backend) return;
  event.preventDefault();
  quitting = true;
  void backend.stop().finally(() => app.quit());
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
