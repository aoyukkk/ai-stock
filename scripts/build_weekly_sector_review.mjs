import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir, qaPath] = process.argv.slice(2);
if (!inputPath || !outputPath || !previewDir || !qaPath) {
  throw new Error("WEEKLY_SECTOR_BUILD_ARGUMENTS_REQUIRED");
}
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const sheetNames = [
  "01_整周板块总览",
  "02_每日板块统计",
  "03_整周推荐明细",
  "04_去重结果明细",
  "05_图表",
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
const num = (value) => (value === null || value === undefined ? "" : Number(value));
const code6 = (value) => String(value || "").split(".")[0].padStart(6, "0");
const setWidth = (sheet, index, width) => {
  sheet.getRange(`${col(index)}:${col(index)}`).format.columnWidth = width;
};
const title = (sheet, endCol, text, note) => {
  sheet.getRange(`A1:${endCol}2`).merge();
  sheet.getRange("A1").values = [[text]];
  sheet.getRange(`A1:${endCol}2`).format = {
    fill: C.navy,
    font: { bold: true, color: C.white, size: 18 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    rowHeight: 28,
  };
  sheet.getRange(`A3:${endCol}3`).merge();
  sheet.getRange("A3").values = [[note]];
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
    borders: { preset: "all", style: "thin", color: C.border },
  };
  const table = sheet.tables.add(`A${startRow}:${endCol}${endRow}`, true, tableName);
  table.style = "TableStyleMedium2";
  table.showBandedRows = false;
  table.showBandedColumns = false;
  return { endRow, endCol };
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
const applyClassColors = (range) => {
  for (const [text, fill, font] of [
    ["强成功", C.green, "#215E21"],
    ["稳定成功", "#EBF1DE", "#215E21"],
    ["机会命中后回吐", C.orange, "#9C5700"],
    ["失败", C.red, "#9C0006"],
    ["待成熟", C.gray, C.muted],
    ["不可交易", C.gray, C.muted],
    ["路径不明确", C.gray, C.muted],
  ]) {
    range.conditionalFormats.add("containsText", {
      text,
      format: { fill, font: { bold: true, color: font } },
    });
  }
};
const applySampleStatus = (range) => {
  range.conditionalFormats.add("containsText", {
    text: "暂无成熟样本",
    format: { fill: C.gray, font: { color: C.muted } },
  });
  range.conditionalFormats.add("containsText", {
    text: "样本极少",
    format: { fill: C.orange, font: { color: "#9C5700" } },
  });
};

const overviewSheet = sheets["01_整周板块总览"];
title(
  overviewSheet,
  "N",
  `${data.start_date} 至 ${data.end_date} 整周推荐板块复盘`,
  `仅统计深度复核分（Pro分）≥${data.minimum_recommendation_score}的今日推荐；按股票去重后的首个合法买入路径计算。`,
);
overviewSheet.getRange("A5:D7").values = [
  ["正式推荐", data.summary.recommendation_count, "去重股票", data.summary.unique_stock_count],
  ["覆盖板块", data.summary.sector_count, "成熟样本", data.summary.mature_count],
  ["当前盈利成功率", data.summary.current_profit_rate, "机会命中率", data.summary.opportunity_hit_rate],
];
overviewSheet.getRange("A5:D7").format = {
  fill: C.light,
  font: { color: C.ink, size: 11 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: C.border },
};
overviewSheet.getRange("A5:A7").format.font = { bold: true, color: C.navy };
overviewSheet.getRange("C5:C7").format.font = { bold: true, color: C.navy };
overviewSheet.getRange("B7:D7").format.numberFormat = "0.00%";
const overviewHeaders = [
  "板块",
  "推荐次数",
  "去重股票数",
  "入选日期",
  "成熟样本",
  "强成功",
  "稳定成功",
  "机会回吐",
  "失败",
  "当前盈利成功率",
  "机会命中率",
  "平均税费后收益",
  "平均最大涨幅",
  "样本状态",
];
const overviewRows = data.sector_rows.map((row) => [
  row.sector,
  row.recommendation_count,
  row.unique_stock_count,
  row.recommendation_dates,
  row.mature_count,
  row.strong_success,
  row.stable_success,
  row.opportunity_hit_giveback,
  row.fail,
  num(row.current_profit_rate),
  num(row.opportunity_hit_rate),
  num(row.average_net_return),
  num(row.average_mfe),
  row.sample_status,
]);
const overviewTable = writeTable(
  overviewSheet,
  9,
  overviewHeaders,
  overviewRows,
  "WeeklySectorOverview",
);
overviewSheet.freezePanes.freezeRows(9);
overviewSheet.getRange(`J10:M${overviewTable.endRow}`).format.numberFormat = "0.00%";
applyReturnColors(overviewSheet.getRange(`L10:M${overviewTable.endRow}`));
applySampleStatus(overviewSheet.getRange(`N10:N${overviewTable.endRow}`));
[12, 10, 11, 30, 11, 9, 9, 10, 8, 15, 13, 15, 13, 14].forEach(
  (width, index) => setWidth(overviewSheet, index + 1, width),
);

const dailySheet = sheets["02_每日板块统计"];
title(
  dailySheet,
  "O",
  "每日推荐板块复盘",
  `每一行代表某个推荐日Pro分≥${data.minimum_recommendation_score}的一个板块；尚未成熟的记录不进入成功率。`,
);
const dailyHeaders = [
  "推荐日",
  "板块",
  "推荐数",
  "推荐股票",
  "成熟样本",
  "强成功",
  "稳定成功",
  "机会回吐",
  "失败",
  "待成熟/排除",
  "当前盈利成功率",
  "机会命中率",
  "平均税费后收益",
  "平均最大涨幅",
  "样本状态",
];
const dailyRows = data.daily_sector_rows.map((row) => [
  row.recommendation_date,
  row.sector,
  row.recommendation_count,
  row.stocks,
  row.mature_count,
  row.strong_success,
  row.stable_success,
  row.opportunity_hit_giveback,
  row.fail,
  row.pending_or_excluded,
  num(row.current_profit_rate),
  num(row.opportunity_hit_rate),
  num(row.average_net_return),
  num(row.average_mfe),
  row.sample_status,
]);
const dailyTable = writeTable(
  dailySheet,
  5,
  dailyHeaders,
  dailyRows,
  "DailySectorStatistics",
);
dailySheet.freezePanes.freezeRows(5);
dailySheet.getRange(`K6:N${dailyTable.endRow}`).format.numberFormat = "0.00%";
applyReturnColors(dailySheet.getRange(`M6:N${dailyTable.endRow}`));
applySampleStatus(dailySheet.getRange(`O6:O${dailyTable.endRow}`));
[13, 12, 9, 34, 11, 9, 9, 10, 8, 12, 15, 13, 15, 13, 14].forEach(
  (width, index) => setWidth(dailySheet, index + 1, width),
);

const recommendationSheet = sheets["03_整周推荐明细"];
title(
  recommendationSheet,
  "P",
  "整周每日正式推荐明细",
  `仅保留Pro分≥${data.minimum_recommendation_score}的每日推荐；同一股票在不同日期被推荐时分别列示。`,
);
const recommendationHeaders = [
  "推荐日",
  "Pro排名",
  "股票代码",
  "股票名称",
  "板块",
  "量化排名",
  "量化分",
  "Pro分",
  "目标买入日",
  "买入/计划价",
  "总涨跌幅(税费后)",
  "历史最大涨幅",
  "最大回撤",
  "四类判定",
  "交易状态",
  "推荐等级",
];
const recommendationRows = data.recommendation_rows.map((row) => [
  row.recommendation_date,
  row.pro_rank,
  code6(row.stock_code),
  row.stock_name,
  row.industry || "未知行业",
  row.quant_rank,
  num(row.quant_score),
  num(row.pro_score),
  row.target_trade_date,
  num(row.entry_price ?? row.planned_entry_price),
  num(row.current_net_return),
  num(row.mfe),
  num(row.mae),
  CLASS_LABELS[row.result_class] || row.result_class,
  row.entry_status,
  row.recommendation_grade,
]);
const recommendationTable = writeTable(
  recommendationSheet,
  5,
  recommendationHeaders,
  recommendationRows,
  "WeeklyRecommendationDetail",
);
recommendationSheet.freezePanes.freezeRows(5);
recommendationSheet.getRange(`C6:C${recommendationTable.endRow}`).format.numberFormat = "@";
recommendationSheet.getRange(`G6:H${recommendationTable.endRow}`).format.numberFormat = "0.00";
recommendationSheet.getRange(`J6:J${recommendationTable.endRow}`).format.numberFormat = "0.00";
recommendationSheet.getRange(`K6:M${recommendationTable.endRow}`).format.numberFormat = "0.00%";
applyReturnColors(recommendationSheet.getRange(`K6:M${recommendationTable.endRow}`));
applyClassColors(recommendationSheet.getRange(`N6:N${recommendationTable.endRow}`));
[13, 9, 11, 12, 12, 10, 10, 9, 13, 12, 16, 13, 12, 18, 13, 12].forEach(
  (width, index) => setWidth(recommendationSheet, index + 1, width),
);

const uniqueSheet = sheets["04_去重结果明细"];
title(
  uniqueSheet,
  "P",
  "整周推荐股票去重结果",
  "同一股票只保留一行，买入与收益使用首个合法买入路径；今日首次入选股票显示待成熟。",
);
const uniqueHeaders = [
  "序号",
  "股票代码",
  "股票名称",
  "板块",
  "首次入选日",
  "全部入选日期",
  "入选次数",
  "买入日",
  "买入价",
  "总涨跌幅(税费后)",
  "历史最大涨幅",
  "最大回撤",
  "强/稳/回吐/失败",
  "交易状态",
  "量化分",
  "Pro分",
];
const uniqueRows = data.overview_rows.map((row, index) => [
  index + 1,
  code6(row.stock_code),
  row.stock_name,
  row.industry || "未知行业",
  row.first_recommendation_date,
  row.all_recommendation_dates,
  row.recommendation_count,
  row.entry_status === "FILLED" ? row.target_trade_date : "",
  num(row.entry_price ?? row.planned_entry_price),
  num(row.current_net_return),
  num(row.mfe),
  num(row.mae),
  CLASS_LABELS[row.result_class] || row.result_class,
  row.entry_status,
  num(row.quant_score),
  num(row.pro_score),
]);
const uniqueTable = writeTable(
  uniqueSheet,
  5,
  uniqueHeaders,
  uniqueRows,
  "WeeklyUniqueReview",
);
uniqueSheet.freezePanes.freezeRows(5);
uniqueSheet.getRange(`B6:B${uniqueTable.endRow}`).format.numberFormat = "@";
uniqueSheet.getRange(`I6:I${uniqueTable.endRow}`).format.numberFormat = "0.00";
uniqueSheet.getRange(`J6:L${uniqueTable.endRow}`).format.numberFormat = "0.00%";
applyReturnColors(uniqueSheet.getRange(`J6:L${uniqueTable.endRow}`));
applyClassColors(uniqueSheet.getRange(`M6:M${uniqueTable.endRow}`));
[7, 11, 12, 12, 13, 30, 9, 13, 10, 16, 13, 12, 18, 13, 10, 9].forEach(
  (width, index) => setWidth(uniqueSheet, index + 1, width),
);

const chartSheet = sheets["05_图表"];
title(
  chartSheet,
  "N",
  "整周推荐板块复盘图表",
  "板块图仅展示成熟样本不少于2只且排序靠前的15个板块；完整50个板块请查看首页表格。",
);
chartSheet.getRange(`A5:C${5 + data.chart_sector_rows.length}`).values = [
  ["板块", "当前盈利成功率", "机会命中率"],
  ...data.chart_sector_rows.map((row) => [
    row.sector,
    row.current_profit_rate,
    row.opportunity_hit_rate,
  ]),
];
chartSheet.getRange("A5:C5").format = {
  fill: C.navy,
  font: { bold: true, color: C.white },
  horizontalAlignment: "center",
};
chartSheet.getRange(`B6:C${5 + data.chart_sector_rows.length}`).format.numberFormat = "0%";
chartSheet.getRange("E5:F9").values = [
  ["四类情况", "数量"],
  ...data.class_rows.map((row) => [row.label, row.count]),
];
chartSheet.getRange("E5:F5").format = {
  fill: C.navy,
  font: { bold: true, color: C.white },
  horizontalAlignment: "center",
};
const sectorChart = chartSheet.charts.add(
  "bar",
  chartSheet.getRange(`A5:C${5 + data.chart_sector_rows.length}`),
);
sectorChart.title = "整周板块历史成功率";
sectorChart.hasLegend = true;
sectorChart.legend = { position: "bottom" };
sectorChart.yAxis = { numberFormatCode: "0%" };
sectorChart.setPosition("H5", "N25");
const pieChart = chartSheet.charts.add("pie", chartSheet.getRange("E5:F9"));
pieChart.title = "整周四类结果占比";
pieChart.hasLegend = true;
pieChart.legend = { position: "right" };
pieChart.setPosition("A26", "G43");
[16, 15, 13, 4, 18, 10].forEach((width, index) =>
  setWidth(chartSheet, index + 1, width),
);
for (let index = 7; index <= 14; index += 1) setWidth(chartSheet, index, 12);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const renderRanges = {
  "01_整周板块总览": "A1:N32",
  "02_每日板块统计": "A1:O32",
  "03_整周推荐明细": "A1:P32",
  "04_去重结果明细": "A1:P32",
  "05_图表": "A1:N44",
};
for (const name of sheetNames) {
  const image = await workbook.render({
    sheetName: name,
    range: renderRanges[name],
    scale: 0.9,
    format: "png",
  });
  await fs.writeFile(
    path.join(previewDir, `${name}.png`),
    new Uint8Array(await image.arrayBuffer()),
  );
}
const inspect = await workbook.inspect({
  kind: "table",
  range: "01_整周板块总览!A1:N32",
  include: "values,formulas",
  tableMaxRows: 32,
  tableMaxCols: 14,
  maxChars: 7000,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
  maxChars: 4000,
});
const formulaErrorCount =
  errors.ndjson.includes('"matchCount":0') ||
  !errors.ndjson.match(/#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/)
    ? 0
    : 1;
const qa = {
  sheet_count: sheetNames.length,
  sheet_names: sheetNames,
  chart_count: 2,
  formula_error_count: formulaErrorCount,
  preview_count: sheetNames.length,
  recommendation_count: data.recommendation_rows.length,
  unique_stock_count: data.overview_rows.length,
  sector_count: data.sector_rows.length,
  status: formulaErrorCount === 0 ? "PASS" : "FAIL",
};
await fs.writeFile(qaPath, JSON.stringify(qa, null, 2), "utf8");
await fs.writeFile(`${outputPath}.inspect.ndjson`, `${inspect.ndjson}\n${errors.ndjson}`, "utf8");
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
process.exit(0);
