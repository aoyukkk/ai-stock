import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [payloadPath, outputPath] = process.argv.slice(2);
if (!payloadPath || !outputPath) throw new Error("payload and output paths are required");
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const workbook = Workbook.create();

const flatten = (value, prefix = "", out = {}) => {
  for (const [key, item] of Object.entries(value || {})) {
    const name = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) flatten(item, name, out);
    else out[name] = Array.isArray(item) ? item.join(" | ") : item;
  }
  return out;
};
const summaryRows = Object.entries(flatten(payload.summary)).map(([metric, value]) => ({ metric, value }));
const byStage = (stage) => payload.daily_metrics.filter((row) => row.stage_type === stage);
const byHorizon = (horizon) => payload.stage_details.filter((row) => Number(row.horizon) === horizon);
const sheets = [
  ["累计总览", summaryRows],
  ["Quant排序效度", byStage("QUANT")],
  ["Flash评分效度", payload.daily_metrics.filter((row) => row.stage_type !== "QUANT")],
  ["Flash选中与未选中", payload.daily_metrics],
  ["Flash相对Quant增量", payload.daily_metrics],
  ["Promote与Demote", payload.promote_demote],
  ["V2与V3对照", payload.daily_metrics],
  ["D1明细", byHorizon(1)],
  ["D3明细", byHorizon(3)],
  ["D5明细", byHorizon(5)],
  ["D10明细", byHorizon(10)],
  ["每日指标", payload.daily_metrics],
  ["工程可靠性", payload.reliability],
  ["数据质量", payload.data_quality],
  ["版本与审计", payload.version_audit],
];
const col = (index) => {
  let n = index + 1; let out = "";
  while (n > 0) { const r = (n - 1) % 26; out = String.fromCharCode(65 + r) + out; n = Math.floor((n - 1) / 26); }
  return out;
};
for (let index = 0; index < sheets.length; index += 1) {
  const [name, rows] = sheets[index];
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const headers = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  if (!headers.length) headers.push("status");
  const matrix = [headers, ...(rows.length ? rows.map((row) => headers.map((key) => row[key] ?? null)) : [["暂无成熟数据"]])];
  const end = col(headers.length - 1);
  const range = sheet.getRange(`A1:${end}${matrix.length}`);
  range.values = matrix;
  range.format = { horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, borders: { preset: "all", style: "thin", color: "#D9E2F3" } };
  sheet.getRange(`A1:${end}1`).format = { fill: "#17365D", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
  sheet.freezePanes.freezeRows(1);
  if (rows.length) {
    const table = sheet.tables.add(`A1:${end}${matrix.length}`, true, `ModelEffectiveness${index + 1}`);
    table.style = "TableStyleMedium2"; table.showFilterButton = true;
  }
  range.format.autofitColumns(); range.format.autofitRows(); range.format.columnWidth = 16;
  headers.forEach((header, columnIndex) => {
    const letter = col(columnIndex);
    if (header === "stock_code") sheet.getRange(`${letter}2:${letter}${matrix.length}`).format.numberFormat = "@";
    if (header.includes("return") || header.includes("rate") || header.includes("ratio") || header.includes("spread") || header.includes("lift")) {
      sheet.getRange(`${letter}2:${letter}${matrix.length}`).format.numberFormat = "0.00%";
    }
  });
}
const audit = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 10000 });
for (const name of payload.required_worksheets) if (!audit.ndjson.includes(name)) throw new Error(`missing sheet: ${name}`);
await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
