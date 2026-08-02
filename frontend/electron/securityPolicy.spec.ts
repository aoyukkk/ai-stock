import { readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { describe, expect, it } from "vitest";

import {
  DEVELOPMENT_RENDERER_URL,
  isAllowedExternalUrl,
  isAllowedRendererUrl,
  isTrustedSenderContext,
  requireBackendProxyRequest,
  requireProvider,
  requireSecretValue
} from "./securityPolicy.js";

const packagedEntry = pathToFileURL(path.resolve("dist/index.html")).toString();

describe("Electron renderer URL policy", () => {
  it("allows only the exact development origin", () => {
    expect(isAllowedRendererUrl(DEVELOPMENT_RENDERER_URL, packagedEntry, false)).toBe(true);
    expect(isAllowedRendererUrl(`${DEVELOPMENT_RENDERER_URL}#/workbench`, packagedEntry, false)).toBe(true);
  });

  it.each([
    "http://127.0.0.1.evil.example:5173/",
    "http://127.0.0.1:5174/",
    "http://localhost.evil.example:5173/",
    "file:///tmp/index.html",
    "javascript:alert(1)"
  ])("rejects lookalike or dangerous renderer URL %s", (url) => {
    expect(isAllowedRendererUrl(url, packagedEntry, false)).toBe(false);
  });

  it("allows only the exact packaged file entry", () => {
    expect(isAllowedRendererUrl(`${packagedEntry}#/workbench`, packagedEntry, true)).toBe(true);
    expect(isAllowedRendererUrl(pathToFileURL(path.resolve("dist/other.html")).toString(), packagedEntry, true)).toBe(false);
  });
});

describe("external URL policy", () => {
  it("allows HTTPS without credentials", () => {
    expect(isAllowedExternalUrl("https://docs.example.test/help")).toBe(true);
  });

  it.each([
    "http://example.test",
    "FILE:///tmp/a",
    "javascript:alert(1)",
    "data:text/plain,test",
    "vbscript:msgbox(1)",
    "shell:open",
    "cmd:/c calc",
    " https://example.test",
    "https://user:password@example.test"
  ])("rejects dangerous protocol or malformed input %s", (url) => {
    expect(isAllowedExternalUrl(url)).toBe(false);
  });
});

describe("IPC trust and runtime schemas", () => {
  it("allows only a trusted main frame", () => {
    expect(isTrustedSenderContext({
      senderId: 7,
      expectedSenderId: 7,
      isMainFrame: true,
      senderUrl: DEVELOPMENT_RENDERER_URL,
      packagedEntry,
      packaged: false
    })).toBe(true);
  });

  it("rejects unknown WebContents, child frames and unknown URLs", () => {
    const base = {
      senderId: 7,
      expectedSenderId: 7,
      isMainFrame: true,
      senderUrl: DEVELOPMENT_RENDERER_URL,
      packagedEntry,
      packaged: false
    };
    expect(isTrustedSenderContext({ ...base, senderId: 8 })).toBe(false);
    expect(isTrustedSenderContext({ ...base, isMainFrame: false })).toBe(false);
    expect(isTrustedSenderContext({ ...base, senderUrl: "https://evil.example/" })).toBe(false);
  });

  it("enforces the provider allowlist", () => {
    expect(requireProvider("tushare")).toBe("tushare");
    expect(requireProvider(" TUSHARE ")).toBe("tushare");
    expect(() => requireProvider("../TOKEN")).toThrow("INVALID_SECRET_PROVIDER");
  });

  it("rejects empty, NUL and oversized secret values without echoing them", () => {
    expect(() => requireSecretValue("")).toThrow("INVALID_SECRET_VALUE");
    expect(() => requireSecretValue("bad\0value")).toThrow("INVALID_SECRET_VALUE");
    const fake = `TEST_ONLY_${"x".repeat(8192)}`;
    expect(() => requireSecretValue(fake)).toThrow("SECRET_VALUE_TOO_LARGE");
    try {
      requireSecretValue(fake);
    } catch (error) {
      expect(String(error)).not.toContain(fake);
    }
  });

  it("allows approved relative backend paths", () => {
    expect(requireBackendProxyRequest({ method: "GET", url: "/api/pools/?limit=1" }).url).toBe("/api/pools/?limit=1");
    expect(requireBackendProxyRequest({ method: "GET", url: "/health" }).url).toBe("/health");
  });

  it.each([
    { method: "GET", url: "https://evil.example/api/" },
    { method: "GET", url: "//evil.example/api/" },
    { method: "GET", url: "/admin" },
    { method: "POST", url: "/api/runtime/shutdown" },
    { method: "POST", url: "/api/workbench/secrets/tushare", data: { value: "TEST_ONLY" } },
    { method: "GET", url: "/api/pools/", headers: { Authorization: "TEST_ONLY" } }
  ])("rejects an unapproved backend request %#", (request) => {
    expect(() => requireBackendProxyRequest(request)).toThrow();
  });
});

describe("main-process hardening declarations", () => {
  const main = readFileSync(path.resolve("electron/main.ts"), "utf8");
  const preload = readFileSync(path.resolve("electron/preload.ts"), "utf8");
  const types = readFileSync(path.resolve("src/vite-env.d.ts"), "utf8");

  it("denies windows, permissions and downloads by default", () => {
    expect(main).toContain('setWindowOpenHandler(() => ({ action: "deny" }))');
    expect(main).toContain("setPermissionCheckHandler(() => false)");
    expect(main).toContain("callback(false)");
    expect(main).toContain('session.defaultSession.on("will-download"');
  });

  it("keeps BrowserWindow hardening and packaged DevTools policy", () => {
    expect(main).toContain("nodeIntegration: false");
    expect(main).toContain("contextIsolation: true");
    expect(main).toContain("sandbox: true");
    expect(main).toContain("devTools: !app.isPackaged");
    expect(main).not.toContain("webSecurity: false");
    expect(main).not.toContain("allowRunningInsecureContent: true");
  });

  it("does not expose a readable session token to the renderer", () => {
    expect(preload).not.toContain("runtime:connection");
    expect(preload).not.toContain("getConnection");
    expect(preload).not.toContain("sessionToken");
    expect(types).not.toContain("sessionToken");
  });

  it("does not expose arbitrary restart commands or log paths", () => {
    expect(preload).toContain('ipcRenderer.invoke("runtime:restart-backend")');
    expect(preload).toContain('ipcRenderer.invoke("runtime:open-logs")');
    expect(preload).not.toMatch(/runtime:restart-backend",\s*[^)]/);
    expect(preload).not.toMatch(/runtime:open-logs",\s*[^)]/);
  });
});
