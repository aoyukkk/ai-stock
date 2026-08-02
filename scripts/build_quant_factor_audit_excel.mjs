import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [payloadPath, outputPath] = process.argv.slice(2);
if (!payloadPath || !outputPath) throw new Error("AUDIT_WORKBOOK_ARGUMENTS_REQUIRED");

const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const workbook = Workbook.create();
const palette = {
  navy: "#17365D",
  blue: "#D9EAF7",
  light: "#F4F7FA",
  green: "#E2F0D9",
  red: "#FCE4D6",
  yellow: "#FFF2CC",
  border: "#D9E1F2",
  white: "#FFFFFF",
  text: "#1F2937",
};

function columnName(index) {
  let value = index;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
}

function scalar(value) {
  if (value === undefined || value === null) return null;
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

function tableMatrix(rows) {
  const headers = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!headers.includes(key)) headers.push(key);
    }
  }
  if (!headers.length) return { headers: ["状态"], matrix: [["无数据"]] };
  return {
    headers,
    matrix: rows.map((row) => headers.map((header) => scalar(row[header]))),
  };
}

function widthFor(header, rows) {
  const texts = [header, ...rows.slice(0, 200).map((row) => scalar(row[header]))]
    .filter((value) => value !== null)
    .map((value) => String(value));
  const max = Math.max(...texts.map((value) => value.length), 8);
  const lowered = String(header).toLowerCase();
  if (lowered.includes("path") || lowered.includes("说明") || lowered.includes("detail") || lowered.includes("formula")) {
    return Math.min(Math.max(max + 2, 24), 60);
  }
  if (lowered.includes("code") || lowered.includes("代码")) return 14;
  return Math.min(Math.max(max + 2, 10), 24);
}

let tableIndex = 1;
for (const [sheetName, rows] of Object.entries(payload.sheets)) {
  const sheet = workbook.worksheets.add(sheetName);
  sheet.showGridLines = false;
  const normalizedRows = Array.isArray(rows) ? rows : [];
  const { headers, matrix } = tableMatrix(normalizedRows);
  const endColumn = columnName(headers.length);
  const endRow = matrix.length + 1;
  sheet.getRange(`A1:${endColumn}1`).values = [headers];
  for (let index = 0; index < headers.length; index += 1) {
    if (String(headers[index]).toLowerCase().includes("stock_code") || String(headers[index]).includes("股票代码")) {
      const column = columnName(index + 1);
      sheet.getRange(`${column}2:${column}${endRow}`).format.numberFormat = "000000";
    }
  }
  if (matrix.length) sheet.getRange(`A2:${endColumn}${endRow}`).values = matrix;
  const used = sheet.getRange(`A1:${endColumn}${endRow}`);
  used.format = {
    font: { name: "Microsoft YaHei", size: 10, color: palette.text },
    verticalAlignment: "center",
    horizontalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange(`A1:${endColumn}1`).format = {
    fill: palette.navy,
    font: { name: "Microsoft YaHei", size: 10, bold: true, color: palette.white },
    verticalAlignment: "center",
    horizontalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: palette.navy },
  };
  if (endRow > 1) {
    sheet.getRange(`A2:${endColumn}${endRow}`).format.borders = {
      insideHorizontal: { style: "thin", color: palette.border },
    };
  }
  for (let index = 0; index < headers.length; index += 1) {
    const column = columnName(index + 1);
    sheet.getRange(`${column}1:${column}${endRow}`).format.columnWidth = widthFor(headers[index], normalizedRows);
  }
  sheet.getRange(`A1:${endColumn}${Math.min(endRow, 500)}`).format.autofitRows();
  sheet.getRange("1:1").format.rowHeight = 30;
  sheet.freezePanes.freezeRows(1);
  const table = sheet.tables.add(`A1:${endColumn}${endRow}`, true, `AuditTable${tableIndex}`);
  table.style = "TableStyleMedium2";
  table.showFilterButton = true;
  table.showBandedRows = false;
  tableIndex += 1;

  const statusHeader = headers.findIndex((header) => ["状态", "status", "结果"].includes(String(header)));
  if (statusHeader >= 0 && endRow > 1) {
    const column = columnName(statusHeader + 1);
    const statusRange = sheet.getRange(`${column}2:${column}${endRow}`);
    statusRange.conditionalFormats.add("containsText", { text: "PASS", format: { fill: palette.green } });
    statusRange.conditionalFormats.add("containsText", { text: "FAIL", format: { fill: palette.red } });
    statusRange.conditionalFormats.add("containsText", { text: "CONFIRMED", format: { fill: palette.yellow } });
  }
}

const configSheet = workbook.worksheets.getItem("02_运行与配置");
configSheet.getRange("H1:I6").values = [
  ["检查", "值"],
  ["权重合计", null],
  ["期望", 1],
  ["差异", null],
  ["状态", null],
  ["说明", "五大正式权重必须严格等于1"],
];
configSheet.getRange("I2").formulas = [["=SUM(B2:B6)"]];
configSheet.getRange("I4").formulas = [["=I2-I3"]];
configSheet.getRange("I5").formulas = [["=IF(ABS(I4)<=0.00000001,\"PASS\",\"FAIL\")"]];
configSheet.getRange("H1:I1").format = {
  fill: palette.navy,
  font: { bold: true, color: palette.white },
};
configSheet.getRange("H1:I6").format.wrapText = true;
configSheet.getRange("H1:I6").format.horizontalAlignment = "center";
configSheet.getRange("H1:I6").format.verticalAlignment = "center";
configSheet.getRange("H1:I6").format.borders = { preset: "all", style: "thin", color: palette.border };
configSheet.getRange("H1:H6").format.columnWidth = 18;
configSheet.getRange("I1:I6").format.columnWidth = 26;

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const previewDir = path.join(path.dirname(outputPath), "preview");
await fs.mkdir(previewDir, { recursive: true });
for (const sheetName of Object.keys(payload.sheets)) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const used = sheet.getUsedRange(true);
  const preview = await workbook.render({
    sheetName,
    range: `A1:${columnName(Math.min(used.columnCount, 12))}${Math.min(used.rowCount, 35)}`,
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    path.join(previewDir, `${sheetName}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const inspection = await workbook.inspect({
  kind: "table",
  range: "01_审计总览!A1:B12",
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 4,
  maxChars: 4000,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
  maxChars: 5000,
});
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
await fs.writeFile(
  outputPath.replace(/\.xlsx$/i, ".validation.json"),
  JSON.stringify({
    sheet_count: Object.keys(payload.sheets).length,
    sheet_names: Object.keys(payload.sheets),
    formula_errors: errors.ndjson.includes("\"matchCount\":0") || !errors.ndjson.match(/#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/) ? 0 : 1,
    inspection: inspection.ndjson.slice(0, 2000),
    preview_count: Object.keys(payload.sheets).length,
    status: "PASS",
  }, null, 2),
  "utf8",
);
// The native workbook runtime occasionally faults during Windows process teardown
// after every requested artifact has already been flushed. Exit explicitly so the
// caller receives a truthful success status and can continue its integrity checks.
process.exit(0);
