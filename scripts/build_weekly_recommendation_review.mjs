import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir, qaPath] = process.argv.slice(2);
if (!inputPath || !outputPath || !previewDir || !qaPath) {
  throw new Error("WEEKLY_REVIEW_BUILD_ARGUMENTS_REQUIRED");
}

const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const dates = data.date_sheets.map((item) => item.trade_date);
const sheetNames = [
  "01_上周推荐总览",
  ...dates.map((date, index) => `${String(index + 2).padStart(2, "0")}_${date}`),
  "08_四类占比",
];
const sheets = Object.fromEntries(
  sheetNames.map((name) => [name, workbook.worksheets.add(name)]),
);

const C = {
  navy: "#17365D",
  white: "#FFFFFF",
  ink: "#1F2933",
  muted: "#64748B",
  light: "#F4F7F9",
  border: "#BDD7EE",
  green: "#E2F0D9",
  stable: "#EBF1DE",
  orange: "#FCE4D6",
  red: "#F4CCCC",
  gray: "#E7E6E6",
};
const CLASS_LABELS = {
  STRONG_SUCCESS: "强成功",
  STABLE_SUCCESS: "稳定成功",
  OPPORTUNITY_HIT_GIVEBACK: "机会命中后回吐",
  FAIL: "失败",
  PENDING: "待成熟",
  NOT_TRADABLE: "不可交易",
  PATH_AMBIGUOUS: "路径不明确",
};

const col = (n) => {
  let result = "";
  while (n > 0) {
    n -= 1;
    result = String.fromCharCode(65 + (n % 26)) + result;
    n = Math.floor(n / 26);
  }
  return result;
};
const safe = (value) => {
  if (value === null || value === undefined) return "";
  if (typeof value === "number" || typeof value === "boolean") return value;
  const text = String(value);
  return /^[=+\-@]/.test(text) ? `'${text}` : text;
};
const sixCode = (value) => String(value || "").split(".")[0].padStart(6, "0");
const num = (value) => (value === null || value === undefined ? "" : Number(value));
const dailyReturn = (row, date) => num((row.daily_returns || {})[date]);
const setWidth = (sheet, index, width) => {
  sheet.getRange(`${col(index)}:${col(index)}`).format.columnWidth = width;
};
const setTitle = (sheet, endCol, title, subtitle) => {
  sheet.getRange(`A1:${endCol}2`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${endCol}2`).format = {
    fill: C.navy,
    font: { bold: true, color: C.white, size: 18 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 28,
  };
  sheet.getRange(`A3:${endCol}3`).merge();
  sheet.getRange("A3").values = [[subtitle]];
  sheet.getRange(`A3:${endCol}3`).format = {
    fill: C.light,
    font: { color: C.muted, size: 10 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 25,
  };
};
const writeTable = (sheet, startRow, headers, rows, tableName) => {
  const endCol = col(headers.length);
  const body = rows.length
    ? rows.map((row) => row.map(safe))
    : [headers.map((_, index) => (index === 0 ? "无记录" : ""))];
  const endRow = startRow + body.length;
  sheet.getRange(`A${startRow}:${endCol}${startRow}`).values = [headers];
  sheet.getRange(`A${startRow}:${endCol}${startRow}`).format = {
    fill: C.navy,
    font: { bold: true, color: C.white, size: 10 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 34,
  };
  sheet.getRange(`A${startRow + 1}:${endCol}${endRow}`).values = body;
  sheet.getRange(`A${startRow + 1}:${endCol}${endRow}`).format = {
    fill: C.white,
    font: { color: C.ink, size: 9 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: {
      insideHorizontal: { style: "thin", color: C.border },
      insideVertical: { style: "thin", color: C.border },
      top: { style: "thin", color: C.border },
      bottom: { style: "thin", color: C.border },
      left: { style: "thin", color: C.border },
      right: { style: "thin", color: C.border },
    },
  };
  const table = sheet.tables.add(`A${startRow}:${endCol}${endRow}`, true, tableName);
  table.style = "TableStyleMedium2";
  table.showBandedRows = false;
  table.showBandedColumns = false;
  return { endRow, endCol };
};
const applyClassColors = (range) => {
  range.conditionalFormats.add("containsText", {
    text: "强成功",
    format: { fill: C.green, font: { bold: true, color: "#215E21" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "稳定成功",
    format: { fill: C.stable, font: { bold: true, color: "#215E21" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "机会命中后回吐",
    format: { fill: C.orange, font: { bold: true, color: "#9C5700" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "失败",
    format: { fill: C.red, font: { bold: true, color: "#9C0006" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "待成熟",
    format: { fill: C.gray, font: { bold: true, color: C.muted } },
  });
  range.conditionalFormats.add("containsText", {
    text: "不可交易",
    format: { fill: C.gray, font: { bold: true, color: C.muted } },
  });
  range.conditionalFormats.add("containsText", {
    text: "路径不明确",
    format: { fill: C.gray, font: { bold: true, color: C.muted } },
  });
};
const formatPercentColumns = (sheet, columns, startRow, endRow) => {
  for (const column of columns) {
    sheet.getRange(`${column}${startRow}:${column}${endRow}`).format.numberFormat = "0.00%;[Red]-0.00%";
  }
};
const applyReturnColors = (range) => {
  range.conditionalFormats.add("cellIs", {
    operator: "greaterThan",
    formula: 0,
    format: { font: { color: "#FF0000" } },
  });
  range.conditionalFormats.add("cellIs", {
    operator: "lessThan",
    formula: 0,
    format: { font: { color: "#008000" } },
  });
};

const overviewSheet = sheets["01_上周推荐总览"];
const overviewHeaders = [
  "序号",
  "股票代码",
  "股票名称",
  "首次入选日",
  "全部入选日期",
  "入选次数",
  "买入日",
  "买入价",
  ...data.overview_return_dates.map((date) => `${date.slice(5)}涨跌幅`),
  "总涨跌幅(税费后)",
  "历史最大涨幅",
  "四类判定",
];
const overviewRows = data.overview_rows.map((row, index) => [
  index + 1,
  sixCode(row.stock_code),
  row.stock_name,
  row.first_recommendation_date,
  row.all_recommendation_dates,
  row.recommendation_count,
  row.entry_status === "FILLED" ? row.target_trade_date : "",
  num(row.entry_price),
  ...data.overview_return_dates.map((date) => dailyReturn(row, date)),
  num(row.current_net_return),
  num(row.mfe),
  CLASS_LABELS[row.result_class] || row.result_class,
]);
setTitle(
  overviewSheet,
  col(overviewHeaders.length),
  "上周推荐股票总览（去重）",
  `${data.review_start_date} 至 ${data.review_end_date}｜仅统计深度复核分（Pro分）≥${data.minimum_recommendation_score}的今日推荐；同一股票仅保留一行。`,
);
const overviewTable = writeTable(
  overviewSheet,
  5,
  overviewHeaders,
  overviewRows,
  "WeeklyUniqueOverview",
);
overviewSheet.freezePanes.freezeRows(5);
overviewSheet.getRange(`B6:B${overviewTable.endRow}`).format.numberFormat = "@";
overviewSheet.getRange(`H6:H${overviewTable.endRow}`).format.numberFormat = "0.00";
formatPercentColumns(
  overviewSheet,
  ["I", "J", "K", "L", "M", "N", "O"],
  6,
  overviewTable.endRow,
);
applyReturnColors(overviewSheet.getRange(`I6:O${overviewTable.endRow}`));
applyClassColors(overviewSheet.getRange(`P6:P${overviewTable.endRow}`));
[
  7, 11, 11, 13, 31, 8, 13, 10,
  12, 12, 12, 12, 12, 15, 13, 18,
].forEach((width, index) => setWidth(overviewSheet, index + 1, width));

const summaryStart = overviewTable.endRow + 3;
overviewSheet.getRange(`A${summaryStart}:C${summaryStart}`).values = [[
  "四类情况",
  "数量",
  "占比",
]];
overviewSheet.getRange(`A${summaryStart}:C${summaryStart}`).format = {
  fill: C.navy,
  font: { bold: true, color: C.white },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
for (let index = 0; index < data.four_classes.length; index += 1) {
  const item = data.four_classes[index];
  const row = summaryStart + index + 1;
  overviewSheet.getRange(`A${row}`).values = [[item.label]];
  overviewSheet.getRange(`B${row}`).formulas = [[
    `=COUNTIF($P$6:$P$${overviewTable.endRow},"${item.label}")`,
  ]];
  overviewSheet.getRange(`C${row}`).formulas = [[
    `=IFERROR(B${row}/SUM($B$${summaryStart + 1}:$B$${summaryStart + 4}),0)`,
  ]];
}
overviewSheet
  .getRange(`A${summaryStart + 1}:C${summaryStart + 4}`)
  .format.borders = { preset: "all", style: "thin", color: C.border };
overviewSheet
  .getRange(`A${summaryStart + 1}:C${summaryStart + 4}`)
  .format.horizontalAlignment = "center";
overviewSheet.getRange(`C${summaryStart + 1}:C${summaryStart + 4}`).format.numberFormat = "0.00%";
applyClassColors(overviewSheet.getRange(`A${summaryStart + 1}:A${summaryStart + 4}`));
overviewSheet.getRange(`A${summaryStart + 6}:P${summaryStart + 6}`).merge();
overviewSheet.getRange(`A${summaryStart + 6}`).values = [[
  "说明：待成熟、不可交易和路径不明确不进入四类比例；最大涨幅为盘中潜在机会，不等于实际卖出收益。",
]];
overviewSheet.getRange(`A${summaryStart + 6}:P${summaryStart + 6}`).format = {
  fill: C.light,
  font: { color: C.muted, italic: true, size: 9 },
  wrapText: true,
  rowHeight: 24,
};

const dailyHeaders = [
  "序号",
  "股票代码",
  "股票名称",
  "量化排名",
  "量化分",
  "Pro排名",
  "Pro分",
  "目标买入日",
  "买入价",
  ...data.overview_return_dates.map((date) => `${date.slice(5)}涨跌幅`),
  "总涨跌幅(税费后)",
  "历史最大涨幅",
  "四类判定",
];
for (let index = 0; index < data.date_sheets.length; index += 1) {
  const item = data.date_sheets[index];
  const sheetName = sheetNames[index + 1];
  const sheet = sheets[sheetName];
  const rows = item.rows.map((row, rowIndex) => [
    rowIndex + 1,
    sixCode(row.stock_code),
    row.stock_name,
    num(row.quant_rank),
    num(row.quant_score),
    num(row.pro_rank),
    num(row.pro_score),
    row.target_trade_date,
    row.entry_status === "FILLED" ? num(row.entry_price) : num(row.planned_entry_price),
    ...data.overview_return_dates.map((date) => dailyReturn(row, date)),
    num(row.current_net_return),
    num(row.mfe),
    CLASS_LABELS[row.result_class] || row.result_class,
  ]);
  setTitle(
    sheet,
    col(dailyHeaders.length),
    `${item.trade_date} 推荐股票`,
    `仅统计当天深度复核分（Pro分）≥${data.minimum_recommendation_score}的今日推荐；未到T+1标记为待成熟。`,
  );
  const table = writeTable(
    sheet,
    5,
    dailyHeaders,
    rows,
    `DailyReview${item.trade_date.replaceAll("-", "")}`,
  );
  sheet.freezePanes.freezeRows(5);
  sheet.getRange(`B6:B${table.endRow}`).format.numberFormat = "@";
  sheet.getRange(`E6:E${table.endRow}`).format.numberFormat = "0.00";
  sheet.getRange(`G6:G${table.endRow}`).format.numberFormat = "0.00";
  sheet.getRange(`I6:I${table.endRow}`).format.numberFormat = "0.00";
  formatPercentColumns(
    sheet,
    ["J", "K", "L", "M", "N", "O", "P"],
    6,
    table.endRow,
  );
  applyReturnColors(sheet.getRange(`J6:P${table.endRow}`));
  applyClassColors(sheet.getRange(`Q6:Q${table.endRow}`));
  [
    7, 11, 11, 10, 10, 9, 9, 13, 10,
    12, 12, 12, 12, 12, 15, 13, 18,
  ].forEach((width, colIndex) => setWidth(sheet, colIndex + 1, width));
}

const pieSheet = sheets["08_四类占比"];
setTitle(
  pieSheet,
  "L",
  "四类成功率占比",
  "按首页去重后的成熟样本统计；待成熟、不可交易和路径不明确不进入分母。",
);
pieSheet.getRange("A5:C5").values = [["四类情况", "数量", "占比"]];
pieSheet.getRange("A5:C5").format = {
  fill: C.navy,
  font: { bold: true, color: C.white },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
for (let index = 0; index < data.four_classes.length; index += 1) {
  const row = 6 + index;
  const overviewRow = summaryStart + index + 1;
  pieSheet.getRange(`A${row}`).formulas = [[`='01_上周推荐总览'!A${overviewRow}`]];
  pieSheet.getRange(`B${row}`).formulas = [[`='01_上周推荐总览'!B${overviewRow}`]];
  pieSheet.getRange(`C${row}`).formulas = [[`='01_上周推荐总览'!C${overviewRow}`]];
}
pieSheet.getRange("A6:C9").format.borders = {
  preset: "all",
  style: "thin",
  color: C.border,
};
pieSheet.getRange("A5:C9").format.horizontalAlignment = "center";
pieSheet.getRange("C6:C9").format.numberFormat = "0.00%";
applyClassColors(pieSheet.getRange("A6:A9"));
setWidth(pieSheet, 1, 22);
setWidth(pieSheet, 2, 12);
setWidth(pieSheet, 3, 12);
for (let index = 4; index <= 12; index += 1) setWidth(pieSheet, index, 12);
const pieChart = pieSheet.charts.add("pie", pieSheet.getRange("A5:B9"));
pieChart.title = "四类结果占比（去重后成熟样本）";
pieChart.hasLegend = true;
pieChart.legend = { position: "right" };
pieChart.setPosition("E5", "L21");
pieSheet.getRange("A12:L13").merge();
pieSheet.getRange("A12").values = [[data.caveat]];
pieSheet.getRange("A12:L13").format = {
  fill: C.light,
  font: { color: C.muted, italic: true, size: 10 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
for (const name of sheetNames) {
  const sheet = sheets[name];
  const used = sheet.getUsedRange(true);
  const maxCols = Math.min(used.columnCount, name === "08_四类占比" ? 12 : 17);
  const maxRows = Math.min(used.rowCount, name === "08_四类占比" ? 22 : 32);
  const image = await workbook.render({
    sheetName: name,
    range: `A1:${col(maxCols)}${maxRows}`,
    scale: 0.9,
    format: "png",
  });
  await fs.writeFile(
    path.join(previewDir, `${name}.png`),
    new Uint8Array(await image.arrayBuffer()),
  );
}
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
  maxChars: 4000,
});
const formulaErrorCount =
  formulaErrors.ndjson.includes('"matchCount":0') ||
  !formulaErrors.ndjson.match(/#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/)
    ? 0
    : 1;
const qa = {
  sheet_count: sheetNames.length,
  sheet_names: sheetNames,
  chart_count: 1,
  formula_error_count: formulaErrorCount,
  preview_count: sheetNames.length,
  unique_stock_count: data.overview_rows.length,
  daily_sheet_count: data.date_sheets.length,
  status: formulaErrorCount === 0 ? "PASS" : "FAIL",
};
await fs.writeFile(qaPath, JSON.stringify(qa, null, 2), "utf8");
await fs.writeFile(
  `${outputPath}.inspect.ndjson`,
  formulaErrors.ndjson,
  "utf8",
);
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
process.exit(0);
