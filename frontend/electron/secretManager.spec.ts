import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: (value: string) => Buffer.from(`TEST_DPAPI:${value}`, "utf8"),
    decryptString: (value: Buffer) => value.toString("utf8").replace(/^TEST_DPAPI:/, "")
  }
}));

import { SecretManager } from "./secretManager.js";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe("SecretManager", () => {
  it("round-trips through safeStorage without persisting plaintext", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "ai-trader-secret-test-"));
    temporaryDirectories.push(directory);
    const manager = new SecretManager(directory);
    const fake = "TEST_ONLY_SECRET_123456";
    await manager.set("tushare", fake);
    expect((await manager.decrypted(["tushare"])).tushare).toBe(fake);
    expect(await readFile(path.join(directory, "secrets.enc.json"), "utf8")).not.toContain(fake);
  });

  it("returns only approved non-sensitive status metadata", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "ai-trader-secret-test-"));
    temporaryDirectories.push(directory);
    const manager = new SecretManager(directory);
    await manager.set("deepseek", "TEST_ONLY_SECRET_123456");
    const status = await manager.status();
    expect(status.deepseek).toMatchObject({
      configured: true,
      provider: "deepseek",
      storage_backend: "electron_safe_storage",
      validation_status: "ENCRYPTED_AT_REST"
    });
    expect(JSON.stringify(status)).not.toMatch(/ciphertext|length|prefix|suffix|hash/i);
  });

  it("does not decrypt a disabled provider", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "ai-trader-secret-test-"));
    temporaryDirectories.push(directory);
    const manager = new SecretManager(directory);
    await manager.set("tushare", "TEST_ONLY_SECRET_123456");
    expect(await manager.decrypted([])).toEqual({});
  });
});
