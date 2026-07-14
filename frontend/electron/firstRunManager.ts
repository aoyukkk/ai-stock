import { app } from "electron";
import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";

import type { DesktopPaths } from "./pathManager.js";

interface SeedFile {
  destination_path: string;
  sha256: string;
  file_size: number;
}

interface SeedManifest {
  seed_version: string;
  sanitized_database_sha256: string;
  included_files: SeedFile[];
  checksum_status: string;
}

export interface FirstRunResult {
  firstRun: boolean;
  seedVersion: string | null;
}

export async function initializeFirstRun(paths: DesktopPaths): Promise<FirstRunResult> {
  await Promise.all(Object.values(paths).filter((value) => value !== paths.database).map((value) => mkdir(value, { recursive: true })));
  await copyDefaultConfig(paths.config);
  const marker = path.join(paths.root, "first-run.json");
  const existingDatabase = await exists(paths.database);
  if (existingDatabase) {
    return { firstRun: false, seedVersion: null };
  }

  const seedRoot = app.isPackaged
    ? path.join(process.resourcesPath, "seed")
    : path.resolve(app.getAppPath(), "..", "build", "seed");
  const manifestPath = path.join(seedRoot, "SEED_DATA_MANIFEST_1.0.0.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as SeedManifest;
  if (manifest.checksum_status !== "PASS") {
    throw new Error("SEED_MANIFEST_NOT_VERIFIED");
  }

  const databaseSource = path.join(seedRoot, "data", "ai_trader_seed.db");
  if (await sha256(databaseSource) !== manifest.sanitized_database_sha256) {
    throw new Error("SEED_DATABASE_CHECKSUM_MISMATCH");
  }
  await mkdir(path.dirname(paths.database), { recursive: true });
  await copyFile(databaseSource, paths.database);

  for (const item of manifest.included_files) {
    const source = path.resolve(seedRoot, item.destination_path);
    if (!source.startsWith(path.resolve(seedRoot) + path.sep)) throw new Error("SEED_PATH_ESCAPE_BLOCKED");
    if (await sha256(source) !== item.sha256 || (await stat(source)).size !== item.file_size) {
      throw new Error(`SEED_FILE_CHECKSUM_MISMATCH:${item.destination_path}`);
    }
    const destination = destinationFor(paths, item.destination_path);
    await mkdir(path.dirname(destination), { recursive: true });
    await copyFile(source, destination);
  }

  await writeFile(marker, JSON.stringify({ initializedAt: new Date().toISOString(), seedVersion: manifest.seed_version }, null, 2), "utf8");
  return { firstRun: true, seedVersion: manifest.seed_version };
}

async function copyDefaultConfig(configDir: string): Promise<void> {
  const sourceRoot = app.isPackaged
    ? path.join(process.resourcesPath, "config")
    : path.resolve(app.getAppPath(), "..", "config");
  const names = [
    "system.yaml", "stock_scan.yaml", "schedule.yaml", "market_data.yaml", "event_trigger.yaml",
    "models.yaml", "agents.yaml", "ai_score.yaml", "llm.yaml", "token_cost.yaml", "risk_rules.yaml",
    "risk.yaml", "order_price.yaml", "memory.yaml", "paper_trading.yaml", "virtual_trading.yaml",
    "quant_factor.yaml", "ui.yaml", "frontend.yaml", "fundamental_research.yaml", "position_sizing.yaml",
    "temporal.yaml", "data_sources.yaml", "review.yaml"
  ];
  await mkdir(configDir, { recursive: true });
  for (const name of names) {
    const destination = path.join(configDir, name);
    if (!(await exists(destination))) await copyFile(path.join(sourceRoot, name), destination);
  }
}

function destinationFor(paths: DesktopPaths, logicalPath: string): string {
  const normalized = logicalPath.replaceAll("\\", "/");
  if (normalized.startsWith("outputs/")) return path.join(paths.root, normalized);
  if (normalized.startsWith("cache/")) return path.join(paths.root, normalized);
  if (normalized.startsWith("reports/")) return path.join(paths.diagnostics, normalized.slice("reports/".length));
  throw new Error(`SEED_DESTINATION_NOT_ALLOWED:${logicalPath}`);
}

async function sha256(filename: string): Promise<string> {
  return createHash("sha256").update(await readFile(filename)).digest("hex");
}

async function exists(filename: string): Promise<boolean> {
  try { await stat(filename); return true; } catch { return false; }
}
