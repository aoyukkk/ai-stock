import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const [payloadPath, outputPath] = process.argv.slice(2);
if (!payloadPath || !outputPath) {
  throw new Error("Usage: node build_ranking_evaluation_excel.mjs PAYLOAD_JSON OUTPUT_XLSX");
}

const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const workbook = Workbook.create();

function flattenObject(value, prefix = "", target = {}) {
  for (const [key, item] of Object.entries(value || {})) {
    const name = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) {
      flattenObject(item, name, target);
    } else {
      target[name] = Array.isArray(item) ? item.join(" | ") : item;
    }
  }
  return target;
}

function selectRows(rows, fields) {
  return (rows || []).map((row) => {
    const value = {};
    for (const field of fields) value[field] = row?.[field] ?? null;
    return value;
  });
}

function groupRows(rows) {
  return (rows || []).map((row) => {
    const returns = row.group_returns || {};
    const counts = row.group_valid_counts || {};
    const coverage = row.group_coverage || {};
    return {
      ranking_trade_date: row.ranking_trade_date,
      horizon: row.horizon,
      G1_return: returns.G1 ?? null,
      G2_return: returns.G2 ?? null,
      G3_return: returns.G3 ?? null,
      G4_return: returns.G4 ?? null,
      G5_return: returns.G5 ?? null,
      G1_count: counts.G1 ?? null,
      G2_count: counts.G2 ?? null,
      G3_count: counts.G3 ?? null,
      G4_count: counts.G4 ?? null,
      G5_count: counts.G5 ?? null,
      G1_coverage: coverage.G1 ?? null,
      G2_coverage: coverage.G2 ?? null,
      G3_coverage: coverage.G3 ?? null,
      G4_coverage: coverage.G4 ?? null,
      G5_coverage: coverage.G5 ?? null,
      monotonicity_label: row.monotonicity_label,
    };
  });
}

function monotonicRows(rows) {
  return (rows || []).map((row) => {
    const spreads = row.adjacent_spreads || {};
    return {
      ranking_trade_date: row.ranking_trade_date,
      horizon: row.horizon,
      "G1-G2": spreads["G1-G2"] ?? null,
      "G2-G3": spreads["G2-G3"] ?? null,
      "G3-G4": spreads["G3-G4"] ?? null,
      "G4-G5": spreads["G4-G5"] ?? null,
      monotonicity_pass_count: row.monotonicity_pass_count,
      monotonicity_label: row.monotonicity_label,
    };
  });
}

const summaryRows = Object.entries(flattenObject(payload.summary)).map(
  ([metric, value]) => ({ metric, value }),
);
const rankIcRows = selectRows(payload.daily_metrics, [
  "ranking_trade_date", "factor_version", "horizon", "return_basis",
  "calculation_status", "valid_sample_count", "missing_sample_count",
  "coverage_ratio", "rank_ic",
]);
const spreadRows = selectRows(payload.daily_metrics, [
  "ranking_trade_date", "factor_version", "horizon",
  "top20_mean_return", "bottom20_mean_return", "spread",
  "top20_valid_count", "bottom20_valid_count",
  "top20_coverage_ratio", "bottom20_coverage_ratio",
]);
const fiveGroupRows = groupRows(payload.daily_metrics);
const monotonicityRows = monotonicRows(payload.daily_metrics);
const auditRows = payload.version_audit || [];

const sheets = [
  ["累计总览", summaryRows],
  ["Rank_IC", rankIcRows],
  ["Top20_Bottom20", spreadRows],
  ["五组收益", fiveGroupRows],
  ["单调性判断", monotonicityRows],
  ["排名日明细", payload.ranking_details || []],
  ["每日五组平均收益", fiveGroupRows],
  ["数据质量", payload.data_quality || []],
  ["版本与审计", auditRows],
];

function excelColumn(index) {
  let value = index + 1;
  let output = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    output = String.fromCharCode(65 + remainder) + output;
    value = Math.floor((value - 1) / 26);
  }
  return output;
}

function normalizedCell(value) {
  if (value === undefined || value === null || Number.isNaN(value)) return null;
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

for (let sheetIndex = 0; sheetIndex < sheets.length; sheetIndex += 1) {
  const [name, rows] = sheets[sheetIndex];
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const headers = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!headers.includes(key)) headers.push(key);
    }
  }
  if (headers.length === 0) headers.push("status");
  const matrix = [
    headers,
    ...(rows.length
      ? rows.map((row) => headers.map((key) => normalizedCell(row[key])))
      : [headers.map((_, index) => (index === 0 ? "暂无成熟数据" : null))]),
  ];
  const endColumn = excelColumn(headers.length - 1);
  const endRow = matrix.length;
  const range = sheet.getRange(`A1:${endColumn}${endRow}`);
  range.values = matrix;
  range.format = {
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  };
  sheet.getRange(`A1:${endColumn}1`).format = {
    fill: "#17365D",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.freezePanes.freezeRows(1);
  if (rows.length > 0) {
    const table = sheet.tables.add(
      `A1:${endColumn}${endRow}`,
      true,
      `RankingEvalTable${sheetIndex + 1}`,
    );
    table.style = "TableStyleMedium2";
    table.showFilterButton = true;
  }
  range.format.autofitColumns();
  range.format.autofitRows();
  range.format.columnWidth = 16;
  const longTextColumns = new Set([
    "detail", "missing_reason", "row_issue_detail", "metric", "value",
  ]);
  headers.forEach((header, columnIndex) => {
    const column = excelColumn(columnIndex);
    if (header === "stock_code" || header === "ts_code") {
      sheet.getRange(`${column}2:${column}${endRow}`).format.numberFormat = "@";
    }
    if (
      header.includes("return")
      || header.includes("coverage")
      || header === "spread"
      || /^G[1-5]-G[1-5]$/.test(header)
    ) {
      sheet.getRange(`${column}2:${column}${endRow}`).format.numberFormat = "0.00%";
    }
    if (header === "rank_ic") {
      sheet.getRange(`${column}2:${column}${endRow}`).format.numberFormat = "0.0000";
    }
    if (longTextColumns.has(header)) {
      sheet.getRange(`${column}1:${column}${endRow}`).format.columnWidth = 28;
    }
  });
}

const sheetAudit = await workbook.inspect({
  kind: "sheet",
  include: "id,name",
  maxChars: 5000,
});
for (const required of payload.required_worksheets || []) {
  if (!sheetAudit.ndjson.includes(required)) {
    throw new Error(`Required worksheet missing: ${required}`);
  }
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const preview = await workbook.render({
  sheetName: "累计总览",
  autoCrop: "all",
  scale: 1,
  format: "png",
});
await fs.writeFile(
  outputPath.replace(/\.xlsx$/i, ".preview.png"),
  new Uint8Array(await preview.arrayBuffer()),
);
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
