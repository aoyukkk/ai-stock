import { contextBridge } from "electron";

contextBridge.exposeInMainWorld("aiTraderShell", {
  appVersion: "0.3.0",
  shell: "electron-basic"
});
