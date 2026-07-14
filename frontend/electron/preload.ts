import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("aiTraderShell", {
  getConnection: () => ipcRenderer.invoke("runtime:connection"),
  getRuntimeStatus: () => ipcRenderer.invoke("runtime:status"),
  restartBackend: () => ipcRenderer.invoke("runtime:restart-backend"),
  openLogs: () => ipcRenderer.invoke("runtime:open-logs"),
  secrets: {
    status: () => ipcRenderer.invoke("secrets:status"),
    set: (provider: string, value: string) => ipcRenderer.invoke("secrets:set", provider, value),
    delete: (provider: string) => ipcRenderer.invoke("secrets:delete", provider),
    test: (provider: string) => ipcRenderer.invoke("secrets:test", provider)
  }
});
