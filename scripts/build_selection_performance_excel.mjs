import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import { applyCenteredAlignment } from "./excel_alignment.mjs";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("usage: node build_selection_performance_excel.mjs INPUT_JSON OUTPUT_XLSX");
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const disclaimer = "本统计反映选股后的市场价格表现，不代表实际成交收益或交易建议。";
const sheets = [
  ["01_选股日汇总", data.cohorts], ["02_组合每日涨跌", data.daily],
  ["03_个股收益明细", data.stocks], ["04_口径与异常", data.methodology],
];
const col = (n) => { let value = ""; while (n > 0) { n--; value = String.fromCharCode(65 + n % 26) + value; n = Math.floor(n / 26); } return value; };
const safe = (value) => value === null || value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : value;
for (const [index, [name, rows]] of sheets.entries()) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const normalized = Array.isArray(rows) ? rows : Object.entries(rows || {}).map(([item, value]) => ({item, value}));
  const headers = normalized.length ? Object.keys(normalized[0]) : ["状态"];
  const endCol = col(headers.length);
  sheet.getRange(`A1:${endCol}1`).merge(); sheet.getRange("A1").values = [[name]];
  sheet.getRange(`A2:${endCol}2`).merge(); sheet.getRange("A2").values = [[disclaimer]];
  sheet.getRange(`A1:${endCol}1`).format = {fill:"#17365D",font:{bold:true,color:"#FFFFFF",size:16},horizontalAlignment:"center",verticalAlignment:"center"};
  sheet.getRange(`A2:${endCol}2`).format = {fill:"#FFF2CC",font:{color:"#7F6000"},horizontalAlignment:"center",verticalAlignment:"center",wrapText:true};
  sheet.getRange(`A4:${endCol}4`).values = [headers];
  const body = normalized.length ? normalized.map((row) => headers.map((header) => safe(row[header]))) : [["暂无数据"]];
  const endRow = 4 + body.length;
  sheet.getRange(`A5:${endCol}${endRow}`).values = body;
  sheet.getRange(`A4:${endCol}4`).format = {fill:"#D9EAF7",font:{bold:true,color:"#17365D"}};
  sheet.tables.add(`A4:${endCol}${endRow}`, true, `SelectionPerformanceTable${index + 1}`).style = "TableStyleMedium2";
  applyCenteredAlignment(sheet, `A4:${endCol}${endRow}`);
  sheet.freezePanes.freezeRows(4);
  headers.forEach((header, i) => {
    const column = col(i + 1);
    sheet.getRange(`${column}:${column}`).format.columnWidth = /summary|status|说明|口径|异常/i.test(header) ? 28 : 16;
    if (/stock_code/i.test(header)) sheet.getRange(`${column}5:${column}${endRow}`).format.numberFormat = "@";
    if (/return|drawdown|win_rate|coverage_ratio|position_percent/i.test(header)) sheet.getRange(`${column}5:${column}${endRow}`).format.numberFormat = "0.00%";
  });
  const positiveColumns = headers.map((header, i) => /return|drawdown/i.test(header) ? col(i + 1) : null).filter(Boolean);
  for (const column of positiveColumns) {
    const range = sheet.getRange(`${column}5:${column}${endRow}`);
    range.conditionalFormats.add("cellValue", {operator:"greaterThan",formula:0,format:{font:{color:"#C00000"}}});
    range.conditionalFormats.add("cellValue", {operator:"lessThan",formula:0,format:{font:{color:"#008000"}}});
  }
}
await fs.mkdir(path.dirname(outputPath), {recursive:true});
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
