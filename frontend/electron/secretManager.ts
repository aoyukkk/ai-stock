import { safeStorage } from "electron";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

type Provider = "tushare" | "deepseek" | "openai";
type SecretFile = Partial<Record<Provider, string>>;

export class SecretManager {
  private readonly filename: string;

  constructor(secretDir: string) {
    this.filename = path.join(secretDir, "secrets.enc.json");
  }

  async status(): Promise<Record<Provider, { configured: boolean }>> {
    const values = await this.read();
    return {
      tushare: { configured: Boolean(values.tushare) },
      deepseek: { configured: Boolean(values.deepseek) },
      openai: { configured: Boolean(values.openai) }
    };
  }

  async set(provider: Provider, value: string): Promise<void> {
    if (value.trim().length < 8) throw new Error("SECRET_TOO_SHORT");
    if (!safeStorage.isEncryptionAvailable()) throw new Error("OS_ENCRYPTION_UNAVAILABLE");
    const values = await this.read();
    values[provider] = safeStorage.encryptString(value.trim()).toString("base64");
    await this.write(values);
  }

  async delete(provider: Provider): Promise<void> {
    const values = await this.read();
    delete values[provider];
    await this.write(values);
  }

  async decrypted(): Promise<Partial<Record<Provider, string>>> {
    if (!safeStorage.isEncryptionAvailable()) return {};
    const values = await this.read();
    const result: Partial<Record<Provider, string>> = {};
    for (const provider of ["tushare", "deepseek", "openai"] as Provider[]) {
      const encrypted = values[provider];
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

