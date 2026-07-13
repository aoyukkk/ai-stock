import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import { applyCenteredAlignment } from "./excel_alignment.mjs";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("缺少输入或输出路径");
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const names = ["今日概览", "重点候选", "挂单与仓位", "基本面摘要", "量化前100", "当前问题"];
const sheets = Object.fromEntries(names.map((name) => [name, workbook.worksheets.add(name)]));

const C = {
  navy: "#17365D", teal: "#2F6B66", white: "#FFFFFF", ink: "#1F2933",
  light: "#F4F7F9", border: "#D6DEE5", blue: "#DCEAF5", green: "#E3F1E6",
  yellow: "#FFF2CC", red: "#F9DEDC", gray: "#E9EDF0", orange: "#FCE4D6",
};
const col = (n) => { let s = ""; while (n > 0) { n--; s = String.fromCharCode(65 + n % 26) + s; n = Math.floor(n / 26); } return s; };
const safe = (value) => {
  if (value === null || value === undefined) return "";
  if (typeof value === "number" || typeof value === "boolean") return value;
  const text = String(value);
  return /^[=+\-@]/.test(text) ? `'${text}` : text;
};
const setWidth = (sheet, index, width) => { sheet.getRange(`${col(index)}:${col(index)}`).format.columnWidth = width; };
const contains = (range, text, fill, font = C.ink) => range.conditionalFormats.add("containsText", {text, format: {fill, font: {color: font, bold: true}}});
const colorScale = (range) => range.conditionalFormats.add("colorScale", {criteria: [
  {type: "lowestValue", color: "#F4CCCC"}, {type: "percentile", value: 50, color: "#FFF2CC"},
  {type: "highestValue", color: "#C6E0B4"},
]});
const tableBlock = (sheet, startRow, headers, rows, tableName) => {
  const endCol = col(headers.length);
  const body = rows.length ? rows.map((row) => row.map(safe)) : [headers.map(() => "")];
  const endRow = startRow + body.length;
  sheet.getRange(`A${startRow}:${endCol}${startRow}`).values = [headers];
  sheet.getRange(`A${startRow}:${endCol}${startRow}`).format = {
    fill: C.navy, font: {bold: true, color: C.white, size: 10}, wrapText: true,
    verticalAlignment: "center", rowHeight: 32,
  };
  sheet.getRange(`A${startRow + 1}:${endCol}${endRow}`).values = body;
  sheet.getRange(`A${startRow + 1}:${endCol}${endRow}`).format = {
    font: {color: C.ink, size: 9}, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true,
    borders: {insideHorizontal: {style: "thin", color: C.border}},
  };
  const table = sheet.tables.add(`A${startRow}:${endCol}${endRow}`, true, tableName);
  table.style = "TableStyleMedium2";
  table.showFilterButton = true;
  applyCenteredAlignment(sheet, `A${startRow}:${endCol}${endRow}`);
  return {endRow, endCol};
};
const titleBand = (sheet, endCol, title, subtitle = "") => {
  sheet.getRange(`A1:${endCol}2`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange("A1").format = {fill: C.navy, font: {bold: true, color: C.white, size: 18}, verticalAlignment: "center", rowHeight: 28};
  if (subtitle) {
    sheet.getRange(`A3:${endCol}3`).merge();
    sheet.getRange("A3").values = [[subtitle]];
    sheet.getRange("A3").format = {fill: C.light, font: {color: C.teal, size: 10}, rowHeight: 22};
  }
};
for (const sheet of Object.values(sheets)) sheet.showGridLines = false;

// 今日概览
const overview = sheets["今日概览"];
titleBand(overview, "L", data.title, `${data.status_line}  观察日期：${data.trade_date}  目标日期：${data.target_date || "待确认"}`);
const card = (labelRange, valueRange, label, formula, fill) => {
  overview.getRange(labelRange).merge(); overview.getRange(valueRange).merge();
  overview.getRange(labelRange.split(":")[0]).values = [[label]];
  overview.getRange(valueRange.split(":")[0]).formulas = [[formula]];
  overview.getRange(labelRange).format = {fill, font: {bold: true, color: C.navy, size: 10}, horizontalAlignment: "center"};
  overview.getRange(valueRange).format = {fill, font: {bold: true, color: C.ink, size: 18}, horizontalAlignment: "center", verticalAlignment: "center", rowHeight: 28};
};
card("A5:C5", "A6:C7", "重点候选", "=COUNTA('重点候选'!B5:B200)", C.blue);
card("D5:F5", "D6:F7", "有仓位建议", `=COUNTIF('挂单与仓位'!V5:V${4 + data.orders.length},\">0\")`, C.green);
card("G5:I5", "G6:I7", "当前问题", "=COUNTA('当前问题'!A5:A100)", C.red);
card("J5:L5", "J6:L7", "二筛股票", `=${data.summary["二筛股票数"]}`, C.yellow);
overview.getRange("A9:L9").merge(); overview.getRange("A9").values = [["优先复核清单"]];
overview.getRange("A9:L9").format = {fill: C.teal, font: {bold: true, color: C.white, size: 11}, rowHeight: 24};
const topHeaders = ["复核排名","股票代码","股票名称","来源","二筛分","深度复核分","结论","建议仓位","参考价","止损价","第二目标价","核心逻辑"];
const topRows = data.top10.map((r) => [r["深度复核排名"],r["股票代码"],r["股票名称"],r["入选来源"],r["二筛得分"],r["深度复核分"],r["二筛结论"],r["建议仓位"],r["参考价"],r["止损价"],r["第二目标价"],r["核心逻辑"]]);
const top = tableBlock(overview, 10, topHeaders, topRows, "OverviewTopCandidates");
overview.freezePanes.freezeRows(3);
overview.getRange(`B11:B${top.endRow}`).format.numberFormat = "@";
overview.getRange(`E11:F${top.endRow}`).format.numberFormat = "0.00";
overview.getRange(`H11:H${top.endRow}`).format.numberFormat = "0.00%";
overview.getRange(`I11:K${top.endRow}`).format.numberFormat = "0.00";
overview.getRange(`L11:L${top.endRow}`).format.wrapText = true;
contains(overview.getRange(`G11:G${top.endRow}`), "优先复核", C.green);
contains(overview.getRange(`G11:G${top.endRow}`), "规则阻断", C.red);
[10,13,14,12,10,12,12,12,11,11,11,42].forEach((w,i)=>setWidth(overview,i+1,w));

// 重点候选
const candidate = sheets["重点候选"];
titleBand(candidate, "V", "重点候选", "按深度复核排名排列；星号表示尚未外部核验的保守归纳。");
const cHeaders = ["复核排名","股票代码","股票名称","入选来源","量化排名","量化得分","二筛得分","二筛结论","深度复核分","复核优先级","一级行业","产业链","财务状态","建议仓位","建议股数","参考价","止损价","第二目标价","风险收益比","核心逻辑","主要风险","当前状态"];
const cRows = data.candidates.map((r)=>[r["深度复核排名"],r["股票代码"],r["股票名称"],r["入选来源"],r["量化排名"],r["量化得分"],r["二筛得分"],r["二筛结论"],r["深度复核分"],r["复核优先级"],r["一级行业"],r["产业链"],r["财务状态"],r["建议仓位"],r["建议股数"],r["参考价"],r["止损价"],r["第二目标价"],r["风险收益比"],r["核心逻辑"],r["主要风险"],r["当前状态"]]);
const ct = tableBlock(candidate,4,cHeaders,cRows,"HumanCandidates");
candidate.freezePanes.freezeRows(4); candidate.freezePanes.freezeColumns(3);
candidate.getRange(`B5:B${ct.endRow}`).format.numberFormat="@";
candidate.getRange(`F5:I${ct.endRow}`).format.numberFormat="0.00";
candidate.getRange(`N5:N${ct.endRow}`).format.numberFormat="0.00%";
candidate.getRange(`O5:O${ct.endRow}`).format.numberFormat="#,##0";
candidate.getRange(`P5:S${ct.endRow}`).format.numberFormat="0.00";
candidate.getRange(`T5:U${ct.endRow}`).format.wrapText=true;
colorScale(candidate.getRange(`G5:G${ct.endRow}`)); colorScale(candidate.getRange(`I5:I${ct.endRow}`));
contains(candidate.getRange(`H5:H${ct.endRow}`),"优先复核",C.green); contains(candidate.getRange(`V5:V${ct.endRow}`),"已阻断",C.red);
for(let i=1;i<=22;i++) setWidth(candidate,i,[20,21].includes(i)?42:[2,3,4,11,12].includes(i)?16:11);

// 挂单与仓位
const order = sheets["挂单与仓位"];
titleBand(order,"X","挂单与仓位","价格与仓位均由本地规则计算；按深度复核排名排列。");
const oHeaders=["复核排名","股票代码","股票名称","入选来源","量化得分","二筛得分","二筛结论","深度复核分","保守价","均衡价","积极价","参考价","最高接受价","止损价","第一目标价","第二目标价","第一目标收益比","第二目标收益比","当前收益比","建议仓位","建议资金","建议股数","预计最大损失","说明"];
const oRows=data.orders.map((r)=>[r["深度复核排名"],r["股票代码"],r["股票名称"],r["入选来源"],r["量化得分"],r["二筛得分"],r["二筛结论"],r["深度复核分"],r["保守价"],r["均衡价"],r["积极价"],r["参考价"],r["最高接受价"],r["止损价"],r["第一目标价"],r["第二目标价"],r["第一目标风险收益比"],r["第二目标风险收益比"],r["当前风险收益比"],r["建议仓位"],r["建议资金"],r["建议股数"],r["预计最大损失"],r["说明"]]);
const ot=tableBlock(order,4,oHeaders,oRows,"HumanOrders");
order.freezePanes.freezeRows(4); order.freezePanes.freezeColumns(3);
order.getRange(`B5:B${ot.endRow}`).format.numberFormat="@"; order.getRange(`E5:S${ot.endRow}`).format.numberFormat="0.00";
order.getRange(`T5:T${ot.endRow}`).format.numberFormat="0.00%"; order.getRange(`U5:U${ot.endRow}`).format.numberFormat="#,##0.00";
order.getRange(`V5:V${ot.endRow}`).format.numberFormat="#,##0"; order.getRange(`W5:W${ot.endRow}`).format.numberFormat="#,##0.00";
order.getRange(`X5:X${ot.endRow}`).format.wrapText=true; colorScale(order.getRange(`S5:S${ot.endRow}`));
contains(order.getRange(`X5:X${ot.endRow}`),"阻断",C.red); contains(order.getRange(`X5:X${ot.endRow}`),"不足一手",C.yellow);
const totalRow=ot.endRow+2; order.getRange(`T${totalRow}:W${totalRow}`).values=[["合计",null,null,null]];
order.getRange(`T${totalRow}:W${totalRow}`).format={fill:C.navy,font:{bold:true,color:C.white}};
order.getRange(`T${totalRow}`).formulas=[[`=SUM(T5:T${ot.endRow})`]]; order.getRange(`U${totalRow}`).formulas=[[`=SUM(U5:U${ot.endRow})`]];
order.getRange(`V${totalRow}`).formulas=[[`=SUM(V5:V${ot.endRow})`]]; order.getRange(`W${totalRow}`).formulas=[[`=SUM(W5:W${ot.endRow})`]];
order.getRange(`T${totalRow}`).format.numberFormat="0.00%"; order.getRange(`U${totalRow}:W${totalRow}`).format.numberFormat="#,##0.00";
for(let i=1;i<=24;i++) setWidth(order,i,i===24?42:[2,3,4].includes(i)?15:11);

// 基本面摘要
const fundamental=sheets["基本面摘要"];
titleBand(fundamental,"S","基本面摘要","星号表示基于现有主营和结构化信息作出的保守归纳，建议重点核验核心逻辑与失效条件。");
const fHeaders=["复核排名","股票代码","股票名称","入选来源","一级行业","产业链","链条位置","主营业务","核心产品","概念标签","结构性方向","潜在优势","行业趋势","核心逻辑","失效条件","财务状态","财务说明","人工复核","复核优先级"];
const fRows=data.fundamentals.map((r)=>[r["深度复核排名"],r["股票代码"],r["股票名称"],r["入选来源"],r["一级行业"],r["产业链"],r["链条位置"],r["主营业务"],r["核心产品"],r["概念标签"],r["结构性方向"],r["潜在优势"],r["行业趋势"],r["核心逻辑"],r["失效条件"],r["财务状态"],r["财务说明"],r["人工复核"],r["复核优先级"]]);
const ft=tableBlock(fundamental,4,fHeaders,fRows,"HumanFundamentals");
fundamental.freezePanes.freezeRows(4); fundamental.freezePanes.freezeColumns(3);
fundamental.getRange(`B5:B${ft.endRow}`).format.numberFormat="@"; fundamental.getRange(`H5:Q${ft.endRow}`).format.wrapText=true;
contains(fundamental.getRange(`P5:P${ft.endRow}`),"承压",C.orange); contains(fundamental.getRange(`R5:R${ft.endRow}`),"是",C.yellow);
for(let i=1;i<=19;i++) setWidth(fundamental,i,[8,9,10,11,12,13,14,15,17].includes(i)?34:[2,3,4,5,6].includes(i)?16:11);

// 量化前100
const quant=sheets["量化前100"];
titleBand(quant,"L","量化前100","用于快速浏览，不包含全市场技术字段。");
const qHeaders=["量化排名","股票代码","股票名称","一级行业","量化总分","技术得分","资金得分","情绪得分","动量得分","风险得分","进入二筛","进入重点候选"];
const qRows=data.quant_top100.map((r)=>[r["量化排名"],r["股票代码"],r["股票名称"],r["一级行业"],r["量化总分"],r["技术得分"],r["资金得分"],r["情绪得分"],r["动量得分"],r["风险得分"],r["进入二筛"],r["进入重点候选"]]);
const qt=tableBlock(quant,4,qHeaders,qRows,"HumanQuantTop100");
quant.freezePanes.freezeRows(4); quant.getRange(`B5:B${qt.endRow}`).format.numberFormat="@";
quant.getRange(`E5:J${qt.endRow}`).format.numberFormat="0.00"; colorScale(quant.getRange(`E5:E${qt.endRow}`));
contains(quant.getRange(`L5:L${qt.endRow}`),"是",C.green); [10,13,15,17,11,11,11,11,11,11,11,14].forEach((w,i)=>setWidth(quant,i+1,w));

// 当前问题
const issues=sheets["当前问题"];
titleBand(issues,"E","当前问题",`当前仅列出仍需处理的事项；历史已解决问题 ${data.summary["历史已解决问题数"]} 项不再重复展示。`);
const iHeaders=["股票代码","股票名称","问题类型","当前状态","说明"];
const iRows=data.issues.map((r)=>[r["股票代码"],r["股票名称"],r["问题类型"],r["当前状态"],r["说明"]]);
const it=tableBlock(issues,4,iHeaders,iRows,"HumanCurrentIssues");
issues.freezePanes.freezeRows(4); issues.getRange(`A5:A${it.endRow}`).format.numberFormat="@"; issues.getRange(`E5:E${it.endRow}`).format.wrapText=true;
contains(issues.getRange(`D5:D${it.endRow}`),"已阻断",C.red); contains(issues.getRange(`D5:D${it.endRow}`),"待人工确认",C.yellow);
[14,16,18,16,52].forEach((w,i)=>setWidth(issues,i+1,w));

await fs.mkdir(path.dirname(outputPath),{recursive:true});
const output=await SpreadsheetFile.exportXlsx(workbook); await output.save(outputPath);

if(previewDir){
  await fs.mkdir(previewDir,{recursive:true});
  const ranges={"今日概览":"A1:L22","重点候选":"A1:V16","挂单与仓位":"A1:X16","基本面摘要":"A1:S12","量化前100":"A1:L18","当前问题":"A1:E12"};
  for(const [sheetName,range] of Object.entries(ranges)){
    const image=await workbook.render({sheetName,range,scale:1.1,format:"png"});
    await fs.writeFile(path.join(previewDir,`${sheetName}.png`),new Uint8Array(await image.arrayBuffer()));
  }
  const errors=await workbook.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:100},summary:"公式错误检查",maxChars:3000});
  await fs.writeFile(path.join(previewDir,"公式检查.ndjson"),errors.ndjson,"utf8");
}
