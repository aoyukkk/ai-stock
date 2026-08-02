import { app } from "electron";
import { readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

import { SecretManager, type Provider } from "./secretManager.js";
import { requireProvider } from "./securityPolicy.js";

const ENV_NAMES: Record<Provider, string> = {
  tushare: "TUSHARE_TOKEN",
  deepseek: "DEEPSEEK_API_KEY",
  openai: "OPENAI_API_KEY",
  tavily: "TAVILY_API_KEY",
  ifind_username: "IFIND_USERNAME",
  ifind_password: "IFIND_PASSWORD",
  ifind_access: "IFIND_ACCESS_TOKEN",
  ifind_refresh: "IFIND_REFRESH_TOKEN"
};

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  await app.whenReady();
  const envFile = path.resolve(process.cwd(), ".env");
  const source = await readFile(envFile, "utf8");
  const parsed = parseEnv(source);
  const selected = options.providers.length ? options.providers : Object.keys(ENV_NAMES) as Provider[];
  const available = selected.filter((provider) => Boolean(parsed.get(ENV_NAMES[provider])));
  process.stdout.write(JSON.stringify({
    mode: options.dryRun ? "DRY_RUN" : "APPLY",
    providers_requested: selected,
    providers_available: available,
    source: ".env",
    secret_values_printed: false
  }, null, 2) + "\n");
  if (options.dryRun) return;

  const manager = new SecretManager(path.join(app.getPath("userData"), "secrets"));
  for (const provider of available) await manager.set(provider, parsed.get(ENV_NAMES[provider]) as string);
  if (options.verify) {
    const roundTrip = await manager.decrypted(available);
    if (available.some((provider) => roundTrip[provider] !== parsed.get(ENV_NAMES[provider]))) {
      throw new Error("DESKTOP_SECRET_MIGRATION_VERIFY_FAILED");
    }
    process.stdout.write("verification=passed\n");
  }
  if (options.removeSource) {
    const names = new Set(available.map((provider) => ENV_NAMES[provider]));
    const filtered = source.split(/\r?\n/).filter((line) => {
      const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=/);
      return !match || !names.has(match[1]);
    }).join("\n");
    const temporary = `${envFile}.desktop-migration.tmp`;
    await writeFile(temporary, filtered, { encoding: "utf8", mode: 0o600 });
    await rename(temporary, envFile);
    process.stdout.write("source_removal=completed\n");
  }
}

function parseArgs(args: string[]): {
  dryRun: boolean;
  verify: boolean;
  removeSource: boolean;
  providers: Provider[];
} {
  const providerIndex = args.indexOf("--providers");
  const providers = providerIndex >= 0
    ? String(args[providerIndex + 1] || "").split(",").filter(Boolean).map(requireProvider)
    : [];
  return {
    dryRun: args.includes("--dry-run"),
    verify: args.includes("--verify"),
    removeSource: args.includes("--remove-source"),
    providers
  };
}

function parseEnv(source: string): Map<string, string> {
  const result = new Map<string, string>();
  for (const line of source.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$/);
    if (!match) continue;
    const raw = match[2].trim();
    const value = raw.length >= 2 && ((raw.startsWith('"') && raw.endsWith('"')) || (raw.startsWith("'") && raw.endsWith("'")))
      ? raw.slice(1, -1)
      : raw;
    result.set(match[1], value);
  }
  return result;
}

void main()
  .catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "DESKTOP_SECRET_MIGRATION_FAILED"}\n`);
    process.exitCode = 1;
  })
  .finally(() => app.quit());
