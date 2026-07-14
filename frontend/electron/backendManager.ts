import { app } from "electron";
import { randomBytes } from "node:crypto";
import { createWriteStream, type WriteStream } from "node:fs";
import { mkdir } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { spawn, type ChildProcessByStdio } from "node:child_process";
import type { Readable } from "node:stream";

import { backendEnvironment, type DesktopPaths } from "./pathManager.js";
import type { SecretManager } from "./secretManager.js";

export interface BackendConnection {
  baseUrl: string;
  sessionToken: string;
  appVersion: string;
}

export class BackendManager {
  private process: ChildProcessByStdio<null, Readable, Readable> | null = null;
  private connection: BackendConnection | null = null;
  private logStream: WriteStream | null = null;

  constructor(private readonly paths: DesktopPaths, private readonly secrets: SecretManager) {}

  currentConnection(): BackendConnection {
    if (!this.connection) throw new Error("BACKEND_NOT_READY");
    return { ...this.connection };
  }

  async start(): Promise<BackendConnection> {
    if (this.process && this.connection) return this.currentConnection();
    const port = await availablePort();
    const token = randomBytes(32).toString("base64url");
    const baseUrl = `http://127.0.0.1:${port}`;
    await mkdir(this.paths.logs, { recursive: true });
    this.logStream = createWriteStream(path.join(this.paths.logs, "backend.log"), { flags: "a" });
    const command = backendCommand();
    const child = spawn(command.executable, command.args, {
      cwd: this.paths.root,
      env: backendEnvironment(this.paths, port, token),
      windowsHide: true,
      detached: false,
      stdio: ["ignore", "pipe", "pipe"]
    });
    this.process = child;
    child.stdout.on("data", (chunk) => this.writeLog(chunk));
    child.stderr.on("data", (chunk) => this.writeLog(chunk));
    child.once("exit", () => { this.process = null; this.connection = null; });
    this.connection = { baseUrl, sessionToken: token, appVersion: app.getVersion() };
    await waitForHealth(baseUrl, child, Number(process.env.AI_TRADER_BACKEND_START_TIMEOUT_MS || 90000));
    await this.injectStoredSecrets();
    return this.currentConnection();
  }

  async stop(): Promise<void> {
    const child = this.process;
    const connection = this.connection;
    this.connection = null;
    if (!child) return;
    if (connection) {
      try {
        await fetch(`${connection.baseUrl}/api/runtime/shutdown`, {
          method: "POST",
          headers: { "X-AI-Trader-Token": connection.sessionToken },
          signal: AbortSignal.timeout(3000)
        });
      } catch { /* process fallback below */ }
    }
    await waitForExit(child, 5000);
    if (this.process && child.pid) {
      await new Promise<void>((resolve) => {
        const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
        killer.once("exit", () => resolve());
        killer.once("error", () => resolve());
      });
    }
    this.process = null;
    this.logStream?.end();
    this.logStream = null;
  }

  async restart(): Promise<BackendConnection> {
    await this.stop();
    return this.start();
  }

  async saveSecret(provider: "tushare" | "deepseek" | "openai", value: string): Promise<void> {
    await this.secrets.set(provider, value);
    await this.injectSecret(provider, value);
  }

  async deleteSecret(provider: "tushare" | "deepseek" | "openai"): Promise<void> {
    await this.secrets.delete(provider);
    const connection = this.currentConnection();
    await fetch(`${connection.baseUrl}/api/runtime/secrets/${provider}`, {
      method: "DELETE",
      headers: { "X-AI-Trader-Token": connection.sessionToken }
    });
  }

  private async injectStoredSecrets(): Promise<void> {
    const values = await this.secrets.decrypted();
    for (const [provider, value] of Object.entries(values)) {
      if (value) await this.injectSecret(provider as "tushare" | "deepseek" | "openai", value);
    }
  }

  private async injectSecret(provider: "tushare" | "deepseek" | "openai", value: string): Promise<void> {
    const connection = this.currentConnection();
    const response = await fetch(`${connection.baseUrl}/api/runtime/secrets`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-AI-Trader-Token": connection.sessionToken },
      body: JSON.stringify({ provider, value })
    });
    if (!response.ok) throw new Error(`SECRET_INJECTION_FAILED:${provider}`);
  }

  private writeLog(chunk: Buffer): void {
    const text = chunk.toString("utf8")
      .replace(/(authorization|token|api[_-]?key|secret)\s*[:=]\s*[^\s,;]+/gi, "$1=[REDACTED]");
    this.logStream?.write(text);
  }
}

function backendCommand(): { executable: string; args: string[] } {
  if (app.isPackaged) {
    return { executable: path.join(process.resourcesPath, "backend", "ai_trader_backend.exe"), args: [] };
  }
  const executable = process.env.AI_TRADER_PYTHON || "python";
  return { executable, args: ["-m", "backend.desktop_entry"] };
}

async function availablePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => port ? resolve(port) : reject(new Error("PORT_SELECTION_FAILED")));
    });
  });
}

async function waitForHealth(baseUrl: string, child: ChildProcessByStdio<null, Readable, Readable>, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`BACKEND_EXITED:${child.exitCode}`);
    try {
      const response = await fetch(`${baseUrl}/health`, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return;
    } catch { /* retry */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("BACKEND_START_TIMEOUT");
}

async function waitForExit(child: ChildProcessByStdio<null, Readable, Readable>, timeoutMs: number): Promise<void> {
  if (child.exitCode !== null) return;
  await Promise.race([
    new Promise<void>((resolve) => child.once("exit", () => resolve())),
    new Promise<void>((resolve) => setTimeout(resolve, timeoutMs))
  ]);
}
