import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir, qaPath] = process.argv.slice(2);
if (!inputPath || !outputPath || !previewDir || !qaPath) {
  throw new Error("TODAY_SECTOR_BUILD_ARGUMENTS_REQUIRED");
}
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const sheetNames = ["01_板块成功率", "02_今日推荐明细", "03_历史样本明细", "04_图表"];
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
  range.conditionalFormats.add("containsText", {
    text: "强成功",
    format: { fill: C.green, font: { bold: true, color: "#215E21" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "稳定成功",
    format: { fill: "#EBF1DE", font: { bold: true, color: "#215E21" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "机会命中后回吐",
    format: { fill: C.orange, font: { bold: true, color: "#9C5700" } },
  });
  range.conditionalFormats.add("containsText", {
    text: "失败",
    format: { fill: C.red, font: { bold: true, color: "#9C0006" } },
  });
};

const sectorSheet = sheets["01_板块成功率"];
title(
  sectorSheet,
  "M",
  `${data.trade_date} 今日推荐板块成功率`,
  `今日推荐口径为深度复核分（Pro分）≥${data.minimum_recommendation_score}；今日自身尚未到T+1，不把待成熟记录算入成功或失败。`,
);
const kpis = [
  ["今日推荐", data.summary.today_recommendation_count, "覆盖板块", data.summary.today_sector_count],
  ["成熟历史样本", data.summary.historical_mature_unique_count, "当前盈利成功率", data.summary.current_profit_rate],
  ["机会命中率", data.summary.opportunity_hit_rate, "平均税费后收益", data.summary.average_net_return],
];
sectorSheet.getRange("A5:D7").values = kpis;
sectorSheet.getRange("A5:D7").format = {
  fill: C.light,
  font: { color: C.ink, size: 11 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: C.border },
};
sectorSheet.getRange("A5:A7").format.font = { bold: true, color: C.navy };
sectorSheet.getRange("C5:C7").format.font = { bold: true, color: C.navy };
sectorSheet.getRange("B7").format.numberFormat = "0.00%";
sectorSheet.getRange("D6:D7").format.numberFormat = "0.00%";
applyReturnColors(sectorSheet.getRange("D7"));
const sectorHeaders = [
  "板块",
  "今日推荐数",
  "今日推荐股票",
  "成熟历史样本",
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
const sectorRows = data.sector_rows.map((row) => [
  row.sector,
  row.today_count,
  row.today_stocks,
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
const sectorTable = writeTable(
  sectorSheet,
  9,
  sectorHeaders,
  sectorRows,
  "TodaySectorSuccess",
);
sectorSheet.freezePanes.freezeRows(9);
sectorSheet.getRange(`I10:L${sectorTable.endRow}`).format.numberFormat = "0.00%";
applyReturnColors(sectorSheet.getRange(`K10:L${sectorTable.endRow}`));
sectorSheet.getRange(`M10:M${sectorTable.endRow}`).conditionalFormats.add("containsText", {
  text: "暂无成熟样本",
  format: { fill: C.gray, font: { color: C.muted } },
});
sectorSheet.getRange(`M10:M${sectorTable.endRow}`).conditionalFormats.add("containsText", {
  text: "样本极少",
  format: { fill: C.orange, font: { color: "#9C5700" } },
});
[12, 10, 35, 12, 9, 9, 10, 8, 15, 13, 15, 13, 14].forEach(
  (width, index) => setWidth(sectorSheet, index + 1, width),
);

const todaySheet = sheets["02_今日推荐明细"];
title(
  todaySheet,
  "N",
  `${data.trade_date} 今日推荐明细`,
  `仅列深度复核分（Pro分）≥${data.minimum_recommendation_score}的今日推荐；空白表示该板块暂无成熟历史样本。`,
);
const todayHeaders = [
  "Pro排名",
  "股票代码",
  "股票名称",
  "板块",
  "量化排名",
  "量化分",
  "Pro分",
  "目标买入日",
  "计划买入价",
  "板块成熟样本",
  "板块当前盈利成功率",
  "板块机会命中率",
  "样本状态",
  "当前状态",
];
const todayRows = data.today_rows.map((row) => [
  row.pro_rank,
  code6(row.stock_code),
  row.stock_name,
  row.sector,
  row.quant_rank,
  num(row.quant_score),
  num(row.pro_score),
  row.target_trade_date,
  num(row.planned_entry_price),
  row.sector_mature_count,
  num(row.sector_current_profit_rate),
  num(row.sector_opportunity_hit_rate),
  row.sector_sample_status,
  "待T+1成熟",
]);
const todayTable = writeTable(
  todaySheet,
  5,
  todayHeaders,
  todayRows,
  "TodayRecommendationSectorDetail",
);
todaySheet.freezePanes.freezeRows(5);
todaySheet.getRange(`B6:B${todayTable.endRow}`).format.numberFormat = "@";
todaySheet.getRange(`F6:G${todayTable.endRow}`).format.numberFormat = "0.00";
todaySheet.getRange(`I6:I${todayTable.endRow}`).format.numberFormat = "0.00";
todaySheet.getRange(`K6:L${todayTable.endRow}`).format.numberFormat = "0.00%";
todaySheet.getRange(`N6:N${todayTable.endRow}`).format = {
  fill: C.gray,
  font: { color: C.muted },
  horizontalAlignment: "center",
};
[9, 11, 12, 12, 10, 10, 9, 13, 12, 13, 18, 15, 14, 13].forEach(
  (width, index) => setWidth(todaySheet, index + 1, width),
);

const historySheet = sheets["03_历史样本明细"];
title(
  historySheet,
  "L",
  "今日板块对应的历史成熟样本",
  "同一股票按首个合法买入路径去重；仅包含今天推荐板块内、截至今日已经成熟且可评价的样本。",
);
const historyHeaders = [
  "序号",
  "首次推荐日",
  "股票代码",
  "股票名称",
  "板块",
  "买入日",
  "买入价",
  "总涨跌幅(税费后)",
  "历史最大涨幅",
  "最大回撤",
  "四类判定",
  "入选次数",
];
const historyRows = data.history_rows.map((row, index) => [
  index + 1,
  row.first_recommendation_date,
  code6(row.stock_code),
  row.stock_name,
  row.industry || "未知行业",
  row.target_trade_date,
  num(row.entry_price),
  num(row.current_net_return),
  num(row.mfe),
  num(row.mae),
  CLASS_LABELS[row.result_class] || row.result_class,
  row.recommendation_count,
]);
const historyTable = writeTable(
  historySheet,
  5,
  historyHeaders,
  historyRows,
  "SectorHistoricalMatureSamples",
);
historySheet.freezePanes.freezeRows(5);
historySheet.getRange(`C6:C${historyTable.endRow}`).format.numberFormat = "@";
historySheet.getRange(`G6:G${historyTable.endRow}`).format.numberFormat = "0.00";
historySheet.getRange(`H6:J${historyTable.endRow}`).format.numberFormat = "0.00%";
applyReturnColors(historySheet.getRange(`H6:J${historyTable.endRow}`));
applyClassColors(historySheet.getRange(`K6:K${historyTable.endRow}`));
[7, 13, 11, 12, 12, 13, 10, 16, 13, 12, 18, 10].forEach(
  (width, index) => setWidth(historySheet, index + 1, width),
);

const chartSheet = sheets["04_图表"];
title(
  chartSheet,
  "N",
  "今日推荐板块成功率图表",
  "左图比较有成熟样本板块的当前盈利成功率与机会命中率；右图显示这些板块历史样本的四类占比。",
);
const chartSectors = data.sector_rows.filter((row) => row.mature_count > 0);
chartSheet.getRange(`A5:C${5 + chartSectors.length}`).values = [
  ["板块", "当前盈利成功率", "机会命中率"],
  ...chartSectors.map((row) => [
    row.sector,
    row.current_profit_rate,
    row.opportunity_hit_rate,
  ]),
];
chartSheet.getRange(`A5:C5`).format = {
  fill: C.navy,
  font: { bold: true, color: C.white },
  horizontalAlignment: "center",
};
chartSheet.getRange(`B6:C${5 + chartSectors.length}`).format.numberFormat = "0%";
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
  chartSheet.getRange(`A5:C${5 + chartSectors.length}`),
);
sectorChart.title = "今日推荐板块历史成功率";
sectorChart.hasLegend = true;
sectorChart.legend = { position: "bottom" };
sectorChart.yAxis = { numberFormatCode: "0%" };
sectorChart.setPosition("H5", "N24");
const classChart = chartSheet.charts.add("pie", chartSheet.getRange("E5:F9"));
classChart.title = "四类历史结果占比";
classChart.hasLegend = true;
classChart.legend = { position: "right" };
classChart.setPosition("A25", "G42");
[16, 15, 13, 4, 18, 10].forEach((width, index) =>
  setWidth(chartSheet, index + 1, width),
);
for (let index = 7; index <= 14; index += 1) setWidth(chartSheet, index, 12);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const renderRanges = {
  "01_板块成功率": `A1:M${Math.min(sectorTable.endRow, 31)}`,
  "02_今日推荐明细": `A1:N${Math.min(todayTable.endRow, 31)}`,
  "03_历史样本明细": `A1:L${Math.min(historyTable.endRow, 32)}`,
  "04_图表": "A1:N43",
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
const keyInspect = await workbook.inspect({
  kind: "table",
  range: `01_板块成功率!A1:M${Math.min(sectorTable.endRow, 31)}`,
  include: "values,formulas",
  tableMaxRows: 31,
  tableMaxCols: 13,
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
  today_recommendation_count: data.today_rows.length,
  today_sector_count: data.sector_rows.length,
  historical_mature_count: data.history_rows.length,
  status: formulaErrorCount === 0 ? "PASS" : "FAIL",
};
await fs.writeFile(qaPath, JSON.stringify(qa, null, 2), "utf8");
await fs.writeFile(`${outputPath}.inspect.ndjson`, `${keyInspect.ndjson}\n${errors.ndjson}`, "utf8");
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
process.exit(0);
