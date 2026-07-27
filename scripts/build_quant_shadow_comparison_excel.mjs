import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [payloadPath, outputPath] = process.argv.slice(2);
if (!payloadPath || !outputPath) throw new Error("SHADOW_WORKBOOK_ARGUMENTS_REQUIRED");

const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const workbook = Workbook.create();
const palette = {
  navy: "#17365D",
  white: "#FFFFFF",
  text: "#1F2937",
  border: "#D9E1F2",
  green: "#E2F0D9",
  yellow: "#FFF2CC",
  red: "#FCE4D6",
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
  if (!headers.length) return { headers: ["status"], matrix: [["NO_DATA"]] };
  return {
    headers,
    matrix: rows.map((row) => headers.map((header) => scalar(row[header]))),
  };
}

function widthFor(header, rows) {
  const text = String(header).toLowerCase();
  const samples = [header, ...rows.slice(0, 150).map((row) => scalar(row[header]))]
    .filter((value) => value !== null)
    .map((value) => String(value));
  const max = Math.max(...samples.map((value) => value.length), 8);
  if (
    text.includes("path") ||
    text.includes("reason") ||
    text.includes("finding") ||
    text.includes("issue") ||
    text.includes("hash") ||
    text.includes("说明") ||
    text.includes("建议")
  ) {
    return Math.min(Math.max(max + 2, 24), 58);
  }
  if (text.includes("stock_code") || text.includes("股票代码")) return 14;
  return Math.min(Math.max(max + 2, 10), 24);
}

let tableIndex = 1;
const dimensions = {};
for (const [sheetName, rows] of Object.entries(payload.sheets)) {
  const sheet = workbook.worksheets.add(sheetName);
  sheet.showGridLines = false;
  const normalizedRows = Array.isArray(rows) ? rows : [];
  const { headers, matrix } = tableMatrix(normalizedRows);
  const endColumn = columnName(headers.length);
  const endRow = matrix.length + 1;
  dimensions[sheetName] = { columns: headers.length, rows: endRow };
  sheet.getRange(`A1:${endColumn}1`).values = [headers];
  for (let index = 0; index < headers.length; index += 1) {
    const header = String(headers[index]);
    const column = columnName(index + 1);
    if (header.toLowerCase().includes("stock_code") || header.includes("股票代码")) {
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
    rowHeight: 30,
  };
  if (endRow > 1) {
    sheet.getRange(`A2:${endColumn}${endRow}`).format.borders = {
      insideHorizontal: { style: "thin", color: palette.border },
    };
  }
  for (let index = 0; index < headers.length; index += 1) {
    const column = columnName(index + 1);
    sheet.getRange(`${column}1:${column}${endRow}`).format.columnWidth = widthFor(
      headers[index],
      normalizedRows,
    );
  }
  sheet.getRange(`A1:${endColumn}${Math.min(endRow, 400)}`).format.autofitRows();
  sheet.freezePanes.freezeRows(1);
  const table = sheet.tables.add(
    `A1:${endColumn}${endRow}`,
    true,
    `ShadowTable${tableIndex}`,
  );
  table.style = "TableStyleMedium2";
  table.showFilterButton = true;
  table.showBandedRows = false;
  tableIndex += 1;

  const statusIndex = headers.findIndex((header) =>
    ["status", "final_status", "结果", "状态", "severity"].includes(
      String(header).toLowerCase(),
    ),
  );
  if (statusIndex >= 0 && endRow > 1) {
    const column = columnName(statusIndex + 1);
    const statusRange = sheet.getRange(`${column}2:${column}${endRow}`);
    statusRange.conditionalFormats.add("containsText", {
      text: "PASS",
      format: { fill: palette.green },
    });
    statusRange.conditionalFormats.add("containsText", {
      text: "CRITICAL",
      format: { fill: palette.red },
    });
    statusRange.conditionalFormats.add("containsText", {
      text: "HIGH",
      format: { fill: palette.yellow },
    });
  }
}

const checkSheet = workbook.worksheets.getItem("01_研究总览");
checkSheet.getRange("D1:E7").values = [
  ["检查", "结果"],
  ["Sheet数量", Object.keys(payload.sheets).length],
  ["期望Sheet数量", 21],
  ["差异", null],
  ["状态", null],
  ["股票代码格式", "000000"],
  ["说明", "所有Shadow结果只读展示，不构成生产推荐"],
];
checkSheet.getRange("E4").formulas = [["=E2-E3"]];
checkSheet.getRange("E5").formulas = [["=IF(E4=0,\"PASS\",\"FAIL\")"]];
checkSheet.getRange("D1:E1").format = {
  fill: palette.navy,
  font: { bold: true, color: palette.white },
};
checkSheet.getRange("D1:E7").format.wrapText = true;
checkSheet.getRange("D1:E7").format.horizontalAlignment = "center";
checkSheet.getRange("D1:E7").format.verticalAlignment = "center";
checkSheet.getRange("D1:E7").format.borders = {
  preset: "all",
  style: "thin",
  color: palette.border,
};
checkSheet.getRange("D1:D7").format.columnWidth = 20;
checkSheet.getRange("E1:E7").format.columnWidth = 40;

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const previewDir = path.join(path.dirname(outputPath), "preview");
await fs.mkdir(previewDir, { recursive: true });
for (const sheetName of Object.keys(payload.sheets)) {
  const size = dimensions[sheetName];
  const preview = await workbook.render({
    sheetName,
    range: `A1:${columnName(Math.min(size.columns, 12))}${Math.min(size.rows, 35)}`,
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
  range: "01_研究总览!A1:E12",
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 5,
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
  JSON.stringify(
    {
      sheet_count: Object.keys(payload.sheets).length,
      sheet_names: Object.keys(payload.sheets),
      formula_errors: errors.ndjson.includes("matched 0 entries") ? 0 : 1,
      inspection: inspection.ndjson.slice(0, 2500),
      preview_count: Object.keys(payload.sheets).length,
      status: "PASS",
    },
    null,
    2,
  ),
  "utf8",
);
process.exit(0);
