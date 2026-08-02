import { safeStorage } from "electron";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { requireProvider, requireSecretValue, SECRET_PROVIDERS, type SecretProvider } from "./securityPolicy.js";

export type Provider = SecretProvider;
type StoredSecret = { ciphertext: string; updated_at: string };
type SecretFile = Partial<Record<Provider, StoredSecret | string>>;
type SecretStatus = {
  configured: boolean;
  provider: Provider;
  storage_backend: "electron_safe_storage";
  updated_at: string | null;
  validation_status: "ENCRYPTED_AT_REST" | "NOT_CONFIGURED" | "OS_ENCRYPTION_UNAVAILABLE";
};

export class SecretManager {
  private readonly filename: string;

  constructor(secretDir: string) {
    this.filename = path.join(secretDir, "secrets.enc.json");
  }

  async status(): Promise<Record<Provider, SecretStatus>> {
    const values = await this.read();
    return Object.fromEntries(SECRET_PROVIDERS.map((provider) => {
      const stored = values[provider];
      const configured = Boolean(stored);
      return [provider, {
        configured,
        provider,
        storage_backend: "electron_safe_storage",
        updated_at: stored && typeof stored !== "string" ? stored.updated_at : null,
        validation_status: !configured
          ? "NOT_CONFIGURED"
          : safeStorage.isEncryptionAvailable() ? "ENCRYPTED_AT_REST" : "OS_ENCRYPTION_UNAVAILABLE"
      }];
    })) as Record<Provider, SecretStatus>;
  }

  async set(provider: Provider, value: string): Promise<void> {
    provider = requireProvider(provider);
    value = requireSecretValue(value);
    const minimumLength = provider === "ifind_username" ? 1 : 8;
    if (value.trim().length < minimumLength) throw new Error("SECRET_TOO_SHORT");
    if (!safeStorage.isEncryptionAvailable()) throw new Error("OS_ENCRYPTION_UNAVAILABLE");
    const values = await this.read();
    values[provider] = {
      ciphertext: safeStorage.encryptString(value.trim()).toString("base64"),
      updated_at: new Date().toISOString()
    };
    await this.write(values);
  }

  async delete(provider: Provider): Promise<void> {
    provider = requireProvider(provider);
    const values = await this.read();
    delete values[provider];
    await this.write(values);
  }

  async decrypted(enabledProviders: readonly Provider[]): Promise<Partial<Record<Provider, string>>> {
    if (!safeStorage.isEncryptionAvailable()) return {};
    const values = await this.read();
    const result: Partial<Record<Provider, string>> = {};
    for (const provider of enabledProviders.map(requireProvider)) {
      const stored = values[provider];
      const encrypted = typeof stored === "string" ? stored : stored?.ciphertext;
      if (encrypted) result[provider] = safeStorage.decryptString(Buffer.from(encrypted, "base64"));
    }
    return result;
  }

  private async read(): Promise<SecretFile> {
    try {
      const parsed = JSON.parse(await readFile(this.filename, "utf8"));
      return parsed && typeof parsed === "object" ? parsed as SecretFile : {};
    } catch {
      return {};
    }
  }

  private async write(values: SecretFile): Promise<void> {
    await mkdir(path.dirname(this.filename), { recursive: true });
    const temporary = `${this.filename}.tmp`;
    await writeFile(temporary, JSON.stringify(values, null, 2), { encoding: "utf8", mode: 0o600 });
    await rename(temporary, this.filename);
  }
}
