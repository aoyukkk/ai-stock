import { app, BrowserWindow, dialog, ipcMain, Notification, shell } from "electron";
import { appendFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { BackendManager } from "./backendManager.js";
import { initializeFirstRun, type FirstRunResult } from "./firstRunManager.js";
import { desktopPaths } from "./pathManager.js";
import { SecretManager } from "./secretManager.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const hasLock = app.requestSingleInstanceLock();
let mainWindow: BrowserWindow | null = null;
let backend: BackendManager | null = null;
let firstRun: FirstRunResult = { firstRun: false, seedVersion: null };
let quitting = false;

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
      sandbox: true
    }
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  if (!app.isPackaged) {
    await mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173");
  } else {
    await mainWindow.loadFile(path.join(__dirname, "../dist/index.html"), { hash: firstRun.firstRun ? "/first-run" : "/workbench" });
  }
}

async function bootstrap(): Promise<void> {
  const paths = desktopPaths();
  await mkdir(paths.logs, { recursive: true });
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
  ipcMain.handle("runtime:connection", () => backend?.currentConnection());
  ipcMain.handle("runtime:status", () => ({ appVersion: app.getVersion(), firstRun, userData: paths.root }));
  ipcMain.handle("runtime:restart-backend", () => backend?.restart());
  ipcMain.handle("runtime:open-logs", () => shell.openPath(paths.logs));
  ipcMain.handle("monitor:notify", (_event, payload: { title?: string; body?: string; severity?: string; stockCode?: string }) => {
    if (!Notification.isSupported()) return { shown: false };
    const notification = new Notification({
      title: String(payload.title || "实时盯盘提醒").slice(0, 120),
      body: String(payload.body || "请打开提醒中心人工复核。").slice(0, 300),
      silent: payload.severity !== "CRITICAL"
    });
    notification.on("click", () => {
      if (!mainWindow) return;
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
      mainWindow.webContents.send("monitor:open-stock", String(payload.stockCode || ""));
    });
    notification.show();
    return { shown: true };
  });
  ipcMain.handle("secrets:status", () => secrets.status());
  ipcMain.handle("secrets:set", (_event, provider, value) => backend?.saveSecret(provider, value));
  ipcMain.handle("secrets:delete", (_event, provider) => backend?.deleteSecret(provider));
  ipcMain.handle("secrets:test", async (_event, provider) => {
    const connection = backend?.currentConnection();
    if (!connection) throw new Error("BACKEND_NOT_READY");
    const response = await fetch(`${connection.baseUrl}/api/workbench/secrets/${provider}/test`, {
      method: "POST",
      headers: { "X-AI-Trader-Token": connection.sessionToken }
    });
    return response.json();
  });
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
