import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [payloadPath, outputPath] = process.argv.slice(2);
if (!payloadPath || !outputPath) throw new Error("QUANT_V2_WORKBOOK_ARGUMENTS_REQUIRED");

const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const workbook = Workbook.create();
const palette = {
  navy: "#17365D",
  white: "#FFFFFF",
  text: "#1F2937",
  border: "#D9E1F2",
  pass: "#E2F0D9",
  warning: "#FFF2CC",
  fail: "#FCE4D6",
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
  return typeof value === "object" ? JSON.stringify(value) : value;
}

function addTable(sheetName, rows) {
  const sheet = workbook.worksheets.add(sheetName);
  sheet.showGridLines = false;
  const headers = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) if (!headers.includes(key)) headers.push(key);
  }
  if (!headers.length) headers.push("status");
  const matrix = rows.length
    ? rows.map((row) => headers.map((header) => scalar(row[header])))
    : [["NO_DATA"]];
  const endColumn = columnName(headers.length);
  const endRow = matrix.length + 1;
  sheet.getRange(`A1:${endColumn}1`).values = [headers];
  sheet.getRange(`A2:${endColumn}${endRow}`).values = matrix;
  sheet.getRange(`A1:${endColumn}${endRow}`).format = {
    font: { name: "Microsoft YaHei", size: 10, color: palette.text },
    verticalAlignment: "center",
    horizontalAlignment: "center",
    wrapText: true,
    borders: { insideHorizontal: { style: "thin", color: palette.border } },
  };
  sheet.getRange(`A1:${endColumn}1`).format = {
    fill: palette.navy,
    font: { name: "Microsoft YaHei", size: 10, bold: true, color: palette.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 30,
  };
  const statusIndex = headers.indexOf("status");
  if (statusIndex >= 0) {
    const column = columnName(statusIndex + 1);
    for (let index = 0; index < rows.length; index += 1) {
      const status = String(rows[index].status || "");
      const fill =
        status === "PASS"
          ? palette.pass
          : status.startsWith("PASS_WITH")
            ? palette.warning
            : status === "FAIL"
              ? palette.fail
              : palette.white;
      sheet.getRange(`${column}${index + 2}`).format.fill = fill;
    }
  }
  for (let index = 0; index < headers.length; index += 1) {
    const header = String(headers[index]).toLowerCase();
    const column = columnName(index + 1);
    const width =
      header.includes("reason") || header.includes("hash") || header.includes("path")
        ? 42
        : header.includes("dataset")
          ? 26
          : 18;
    sheet.getRange(`${column}1:${column}${endRow}`).format.columnWidth = width;
  }
  sheet.freezePanes.freezeRows(1);
  sheet.getRange(`A1:${endColumn}${endRow}`).format.autofitRows();
}

addTable("数据门禁", payload.rows || []);
addTable("运行摘要", [
  { item: "Phase", value: payload.title },
  { item: "Trade Date", value: payload.trade_date },
  { item: "Final Status", value: payload.status },
  { item: "Stop Reason", value: payload.message },
  { item: "Orders", value: payload.runtime?.orders_created ?? 0 },
  { item: "Scheduler", value: payload.runtime?.scheduler ?? "OFF" },
]);
addTable(
  "Hash审计",
  Object.entries(payload.hashes || {}).map(([item, value]) => ({ item, value })),
);

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
console.log(JSON.stringify({ outputPath, sheets: 3 }));
