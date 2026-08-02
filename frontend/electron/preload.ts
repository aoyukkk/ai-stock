import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("aiTraderShell", {
  backend: {
    request: (request: { method: string; url: string; data?: unknown; params?: Record<string, unknown> }) =>
      ipcRenderer.invoke("backend:request", request)
  },
  getRuntimeStatus: () => ipcRenderer.invoke("runtime:status"),
  restartBackend: () => ipcRenderer.invoke("runtime:restart-backend"),
  openLogs: () => ipcRenderer.invoke("runtime:open-logs"),
  openExternal: (url: string) => ipcRenderer.invoke("runtime:open-external", url),
  notifyMonitorAlert: (payload: { title: string; body: string; severity: string; stockCode: string }) => ipcRenderer.invoke("monitor:notify", payload),
  onOpenMonitorStock: (callback: (stockCode: string) => void) => {
    const listener = (_event: unknown, stockCode: string) => callback(stockCode);
    ipcRenderer.on("monitor:open-stock", listener);
    return () => ipcRenderer.removeListener("monitor:open-stock", listener);
  },
  secrets: {
    status: () => ipcRenderer.invoke("secrets:status"),
    set: (provider: string, value: string) => ipcRenderer.invoke("secrets:set", provider, value),
    delete: (provider: string) => ipcRenderer.invoke("secrets:delete", provider),
    test: (provider: string) => ipcRenderer.invoke("secrets:test", provider)
  }
});
