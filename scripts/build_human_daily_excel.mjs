import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import { applyCenteredAlignment } from "./excel_alignment.mjs";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("缺少输入或输出路径");
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const isMidday = data.output_mode === "midday";
const priceSheetName = isMidday ? "价格与权重" : "挂单与仓位";
const contextSheetName = isMidday ? "复核依据" : "基本面摘要";
const minimumRecommendationScore = Number(data.minimum_recommendation_score ?? 60);
const recommendations = Array.isArray(data.recommendations)
  ? data.recommendations
  : (data.candidates || []).filter((row) => Number(row["深度复核分"]) >= minimumRecommendationScore);
const names = ["今日概览", "今日推荐", "重点候选", priceSheetName, contextSheetName, "量化前100", "当前问题"];
const sheets = Object.fromEntries(names.map((name) => [name, workbook.worksheets.add(name)]));

const C = {
  navy: "#17365D", teal: "#2F6B66", white: "#FFFFFF", ink: "#1F2933",
  light: "#F4F7F9", border: "#D6DEE5", blue: "#DCEAF5", green: "#E3F1E6",
  yellow: "#FFF2CC", red: "#F9DEDC", gray: "#E9EDF0", orange: "#FCE4D6",
  manualSource: "#FFF2CC", modelSource: "#DCEAF5",
};
const col = (n) => { let s = ""; while (n > 0) { n--; s = String.fromCharCode(65 + n % 26) + s; n = Math.floor(n / 26); } return s; };
const safe = (value) => {
  if (value === null || value === undefined) return "";
  if (typeof value === "number" || typeof value === "boolean") return value;
  const text = String(value);
  return /^[=+\-@]/.test(text) ? `'${text}` : text;
};
const codeCell = (value) => {
  const digits = String(value ?? "").replace(/\D/g, "").slice(0, 6);
  return /^\d{6}$/.test(digits) ? Number(digits) : safe(value);
};
const setWidth = (sheet, index, width) => { sheet.getRange(`${col(index)}:${col(index)}`).format.columnWidth = width; };
const contains = (range, text, fill, font = C.ink) => range.conditionalFormats.add("containsText", {text, format: {fill, font: {color: font, bold: true}}});
const colorScale = (range) => range.conditionalFormats.add("colorScale", {criteria: [
  {type: "lowestValue", color: "#F4CCCC"}, {type: "percentile", value: 50, color: "#FFF2CC"},
  {type: "highestValue", color: "#C6E0B4"},
]});
const sourceFill = (value) => {
  const text = String(value ?? "").trim().toUpperCase();
  if (!text) return C.white;
  return /人工|共同|MANUAL|BOTH|HUMAN/.test(text) ? C.manualSource : C.modelSource;
};
const tableBlock = (sheet, startRow, headers, rows, tableName, emptyLabel = "无待处理事项") => {
  const endCol = col(headers.length);
  const body = rows.length
    ? rows.map((row) => row.map(safe))
    : [headers.map((_, index) => index === 0 ? emptyLabel : "")];
  const endRow = startRow + body.length;
  sheet.getRange(`A${startRow}:${endCol}${startRow}`).values = [headers];
  sheet.getRange(`A${startRow}:${endCol}${startRow}`).format = {
    fill: C.navy, font: {bold: true, color: C.white, size: 10}, wrapText: true,
    verticalAlignment: "center", rowHeight: 32,
  };
  sheet.getRange(`A${startRow + 1}:${endCol}${endRow}`).values = body;
  sheet.getRange(`A${startRow + 1}:${endCol}${endRow}`).format = {
    fill: C.white, font: {color: C.ink, size: 9}, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true,
    borders: {insideHorizontal: {style: "thin", color: C.border}},
  };
  const table = sheet.tables.add(`A${startRow}:${endCol}${endRow}`, true, tableName);
  table.style = "TableStyleMedium2";
  table.showBandedRows = false;
  table.showBandedColumns = false;
  table.showFilterButton = true;
  const sourceIndex = headers.findIndex((header) => ["入选来源", "来源", "选择来源", "交易候选来源"].includes(header));
  if (sourceIndex >= 0) {
    body.forEach((row, index) => {
      sheet.getRange(`A${startRow + 1 + index}:${endCol}${startRow + 1 + index}`).format.fill =
        sourceFill(row[sourceIndex]);
    });
  }
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
card("A4:C4", "A5:C6", "重点候选", "=COUNTA('重点候选'!B5:B200)", C.blue);
const positionedCountFormula = data.summary["非零仓位数量"] === undefined
  ? `=COUNTIF('挂单与仓位'!V5:V${4 + data.orders.length},\">0\")`
  : `=${data.summary["非零仓位数量"]}`;
card("D4:F4", "D5:F6", isMidday ? "等权候选" : "有仓位建议", positionedCountFormula, C.green);
card(
  "G4:I4",
  "G5:I6",
  "当前问题",
  `=${Number(data.summary["当前问题数"] || 0)}`,
  C.red,
);
card("J4:L4", "J5:L6", "今日推荐", `=${recommendations.length}`, C.yellow);
overview.getRange("A7:L7").merge(); overview.getRange("A7").values = [["优先复核清单"]];
overview.getRange("A7:L7").format = {fill: C.teal, font: {bold: true, color: C.white, size: 11}, rowHeight: 24};
const topHeaders = ["复核排名","股票代码","股票名称","来源","二筛分","深度复核分","结论","建议仓位","参考价","止损价","第二目标价","核心逻辑"];
const topRows = data.top10.map((r) => [r["深度复核排名"],codeCell(r["股票代码"]),r["股票名称"],r["入选来源"],r["二筛得分"],r["深度复核分"],r["二筛结论"],r["建议仓位"],r["参考价"],r["止损价"],r["第二目标价"],r["核心逻辑"]]);
const top = tableBlock(overview, 8, topHeaders, topRows, "OverviewTopCandidates");
overview.freezePanes.freezeRows(3);
overview.getRange(`B9:B${top.endRow}`).format.numberFormat = "000000";
overview.getRange(`E9:F${top.endRow}`).format.numberFormat = "0.00";
overview.getRange(`H9:H${top.endRow}`).format.numberFormat = "0.00%";
overview.getRange(`I9:K${top.endRow}`).format.numberFormat = "0.00";
overview.getRange(`L9:L${top.endRow}`).format.wrapText = true;
contains(overview.getRange(`G9:G${top.endRow}`), "优先复核", C.green);
contains(overview.getRange(`G9:G${top.endRow}`), "规则阻断", C.red);
[10,13,14,12,10,12,12,12,11,11,11,42].forEach((w,i)=>setWidth(overview,i+1,w));

// 今日推荐
const recommendation = sheets["今日推荐"];
titleBand(
  recommendation,
  "K",
  "今日推荐",
  `从重点候选中保留最终复核分不低于 ${minimumRecommendationScore.toFixed(0)} 分的股票；模型筛选、人工关注和共同入选使用同一门槛。`,
);
const rHeaders = ["复核排名","股票代码","股票名称","入选来源","最终复核分","复核优先级","一级行业","产业链","财务状态","核心逻辑","主要风险"];
const rRows = recommendations.map((row) => [
  row["深度复核排名"], codeCell(row["股票代码"]), row["股票名称"], row["入选来源"],
  row["深度复核分"], row["复核优先级"], row["一级行业"], row["产业链"],
  row["财务状态"], row["核心逻辑"], row["主要风险"],
]);
const rt = tableBlock(recommendation, 4, rHeaders, rRows, "HumanDailyRecommendations", "暂无推荐");
recommendation.freezePanes.freezeRows(4); recommendation.freezePanes.freezeColumns(3);
recommendation.getRange(`B5:B${rt.endRow}`).format.numberFormat = "000000";
recommendation.getRange(`E5:E${rt.endRow}`).format.numberFormat = "0.00";
recommendation.getRange(`J5:K${rt.endRow}`).format.wrapText = true;
if (recommendations.length) colorScale(recommendation.getRange(`E5:E${rt.endRow}`));
[10,13,15,14,12,12,16,22,12,46,42].forEach((w,i)=>setWidth(recommendation,i+1,w));

// 重点候选
const candidate = sheets["重点候选"];
titleBand(candidate, "V", "重点候选", "完整展示模型前20与全部人工候选，按股票代码去重；无量化基线的人工候选保留展示但不补造评分。");
const cHeaders = ["复核排名","股票代码","股票名称","入选来源","量化排名","量化得分","二筛得分","二筛结论","深度复核分","复核优先级","一级行业","产业链","财务状态","建议仓位","建议股数","参考价","止损价","第二目标价","风险收益比","核心逻辑","主要风险","当前状态"];
const cRows = data.candidates.map((r)=>[r["深度复核排名"],codeCell(r["股票代码"]),r["股票名称"],r["入选来源"],r["量化排名"],r["量化得分"],r["二筛得分"],r["二筛结论"],r["深度复核分"],r["复核优先级"],r["一级行业"],r["产业链"],r["财务状态"],r["建议仓位"],r["建议股数"],r["参考价"],r["止损价"],r["第二目标价"],r["风险收益比"],r["核心逻辑"],r["主要风险"],r["当前状态"]]);
const ct = tableBlock(candidate,4,cHeaders,cRows,"HumanCandidates");
candidate.freezePanes.freezeRows(4); candidate.freezePanes.freezeColumns(3);
candidate.getRange(`B5:B${ct.endRow}`).format.numberFormat="000000";
candidate.getRange(`F5:I${ct.endRow}`).format.numberFormat="0.00";
candidate.getRange(`N5:N${ct.endRow}`).format.numberFormat="0.00%";
candidate.getRange(`O5:O${ct.endRow}`).format.numberFormat="#,##0";
candidate.getRange(`P5:S${ct.endRow}`).format.numberFormat="0.00";
candidate.getRange(`T5:U${ct.endRow}`).format.wrapText=true;
colorScale(candidate.getRange(`G5:G${ct.endRow}`)); colorScale(candidate.getRange(`I5:I${ct.endRow}`));
contains(candidate.getRange(`H5:H${ct.endRow}`),"优先复核",C.green); contains(candidate.getRange(`V5:V${ct.endRow}`),"已阻断",C.red);
for(let i=1;i<=22;i++) setWidth(candidate,i,[20,21].includes(i)?42:[2,3,4,11,12].includes(i)?16:11);

// 挂单与仓位
const order = sheets[priceSheetName];
titleBand(order,isMidday?"R":"X",priceSheetName,isMidday?"价格由本地规则计算；最终候选采用等权参考。":"价格与仓位均由本地规则计算；按深度复核排名排列。");
const oHeaders=isMidday
  ? ["复核排名","股票代码","股票名称","入选来源","量化得分","二筛得分","二筛结论","深度复核分","参考价","最高接受价","止损价","第一目标价","第二目标价","第一目标收益比","第二目标收益比","当前收益比","建议仓位","说明"]
  : ["复核排名","股票代码","股票名称","入选来源","量化得分","二筛得分","二筛结论","深度复核分","保守价","均衡价","积极价","参考价","最高接受价","止损价","第一目标价","第二目标价","第一目标收益比","第二目标收益比","当前收益比","建议仓位","建议资金","建议股数","预计最大损失","说明"];
const oRows=data.orders.map((r)=>isMidday
  ? [r["深度复核排名"],codeCell(r["股票代码"]),r["股票名称"],r["入选来源"],r["量化得分"],r["二筛得分"],r["二筛结论"],r["深度复核分"],r["参考价"],r["最高接受价"],r["止损价"],r["第一目标价"],r["第二目标价"],r["第一目标风险收益比"],r["第二目标风险收益比"],r["当前风险收益比"],r["建议仓位"],r["说明"]]
  : [r["深度复核排名"],codeCell(r["股票代码"]),r["股票名称"],r["入选来源"],r["量化得分"],r["二筛得分"],r["二筛结论"],r["深度复核分"],r["保守价"],r["均衡价"],r["积极价"],r["参考价"],r["最高接受价"],r["止损价"],r["第一目标价"],r["第二目标价"],r["第一目标风险收益比"],r["第二目标风险收益比"],r["当前风险收益比"],r["建议仓位"],r["建议资金"],r["建议股数"],r["预计最大损失"],r["说明"]]);
const ot=tableBlock(order,4,oHeaders,oRows,"HumanOrders");
order.freezePanes.freezeRows(4); order.freezePanes.freezeColumns(3);
order.getRange(`B5:B${ot.endRow}`).format.numberFormat="000000";
if(isMidday){
  order.getRange(`E5:P${ot.endRow}`).format.numberFormat="0.00";
  order.getRange(`Q5:Q${ot.endRow}`).format.numberFormat="0.00%";
  order.getRange(`R5:R${ot.endRow}`).format.wrapText=true;
  colorScale(order.getRange(`P5:P${ot.endRow}`));
  const totalRow=ot.endRow+1;
  order.getRange(`P${totalRow}:Q${totalRow}`).values=[["权重合计",null]];
  order.getRange(`P${totalRow}:Q${totalRow}`).format={fill:C.navy,font:{bold:true,color:C.white},horizontalAlignment:"center",verticalAlignment:"center"};
  order.getRange(`Q${totalRow}`).formulas=[[`=SUM(Q5:Q${ot.endRow})`]];
  order.getRange(`Q${totalRow}`).format.numberFormat="0.00%";
  for(let i=1;i<=18;i++) setWidth(order,i,i===18?38:[2,3,4].includes(i)?15:11);
}else{
  order.getRange(`E5:S${ot.endRow}`).format.numberFormat="0.00";
  order.getRange(`T5:T${ot.endRow}`).format.numberFormat="0.00%"; order.getRange(`U5:U${ot.endRow}`).format.numberFormat="#,##0.00";
  order.getRange(`V5:V${ot.endRow}`).format.numberFormat="#,##0"; order.getRange(`W5:W${ot.endRow}`).format.numberFormat="#,##0.00";
  order.getRange(`X5:X${ot.endRow}`).format.wrapText=true; colorScale(order.getRange(`S5:S${ot.endRow}`));
  contains(order.getRange(`X5:X${ot.endRow}`),"阻断",C.red); contains(order.getRange(`X5:X${ot.endRow}`),"不足一手",C.yellow);
  const totalRow=ot.endRow+1; order.getRange(`T${totalRow}:W${totalRow}`).values=[["合计",null,null,null]];
  order.getRange(`T${totalRow}:W${totalRow}`).format={fill:C.navy,font:{bold:true,color:C.white}};
  order.getRange(`T${totalRow}`).formulas=[[`=SUM(T5:T${ot.endRow})`]]; order.getRange(`U${totalRow}`).formulas=[[`=SUM(U5:U${ot.endRow})`]];
  order.getRange(`V${totalRow}`).formulas=[[`=SUM(V5:V${ot.endRow})`]]; order.getRange(`W${totalRow}`).formulas=[[`=SUM(W5:W${ot.endRow})`]];
  order.getRange(`T${totalRow}`).format.numberFormat="0.00%"; order.getRange(`U${totalRow}:W${totalRow}`).format.numberFormat="#,##0.00";
  for(let i=1;i<=24;i++) setWidth(order,i,i===24?42:[2,3,4].includes(i)?15:11);
}

// 基本面摘要
const fundamental=sheets[contextSheetName];
titleBand(fundamental,isMidday?"L":"T",contextSheetName,isMidday?"汇总午间数据范围、入选逻辑和主要风险，便于快速人工复核。":"基本面由结构化数据与获准重跑的 LLM 补全；“已联网核验”行附公开来源，仍需结合公告原文判断。");
const fHeaders=isMidday
  ? ["复核排名","股票代码","股票名称","入选来源","一级行业","数据范围","入选逻辑","主要风险","资料状态","财务说明","人工复核","复核优先级"]
  : ["复核排名","股票代码","股票名称","入选来源","一级行业","产业链","链条位置","主营业务","核心产品","概念标签","结构性方向","潜在优势","行业趋势","核心逻辑","失效条件","财务状态","财务说明","人工复核","复核优先级","核验来源"];
const fRows=data.fundamentals.map((r)=>isMidday
  ? [r["深度复核排名"],codeCell(r["股票代码"]),r["股票名称"],r["入选来源"],r["一级行业"],r["结构性方向"],r["核心逻辑"],r["失效条件"],r["财务状态"],r["财务说明"],r["人工复核"],r["复核优先级"]]
  : [r["深度复核排名"],codeCell(r["股票代码"]),r["股票名称"],r["入选来源"],r["一级行业"],r["产业链"],r["链条位置"],r["主营业务"],r["核心产品"],r["概念标签"],r["结构性方向"],r["潜在优势"],r["行业趋势"],r["核心逻辑"],r["失效条件"],r["财务状态"],r["财务说明"],r["人工复核"],r["复核优先级"],r["核验来源"]]);
const ft=tableBlock(fundamental,4,fHeaders,fRows,"HumanFundamentals");
fundamental.freezePanes.freezeRows(4); fundamental.freezePanes.freezeColumns(3);
fundamental.getRange(`B5:B${ft.endRow}`).format.numberFormat="000000";
if(isMidday){
  fundamental.getRange(`G5:J${ft.endRow}`).format.wrapText=true;
  fundamental.getRange(`A5:L${ft.endRow}`).format.rowHeight=54;
  contains(fundamental.getRange(`K5:K${ft.endRow}`),"需要",C.yellow);
  contains(fundamental.getRange(`K5:K${ft.endRow}`),"已联网核验",C.green);
  for(let i=1;i<=12;i++) setWidth(fundamental,i,[7,8,10].includes(i)?38:[2,3,4,5,6,9].includes(i)?16:11);
}else{
  fundamental.getRange(`H5:T${ft.endRow}`).format.wrapText=true;
  contains(fundamental.getRange(`P5:P${ft.endRow}`),"承压",C.orange); contains(fundamental.getRange(`R5:R${ft.endRow}`),"需要",C.yellow);
  contains(fundamental.getRange(`R5:R${ft.endRow}`),"已联网核验",C.green);
  for(let i=1;i<=20;i++) setWidth(fundamental,i,i===20?48:[8,9,10,11,12,13,14,15,17].includes(i)?34:[2,3,4,5,6].includes(i)?16:11);
}

// 量化前100
const quant=sheets["量化前100"];
titleBand(quant,"L","量化前100","用于快速浏览，不包含全市场技术字段。");
const qHeaders=["量化排名","股票代码","股票名称","一级行业","量化总分","技术得分","资金得分","情绪得分","动量得分","风险得分","进入二筛","进入重点候选"];
const qRows=data.quant_top100.map((r)=>[r["量化排名"],codeCell(r["股票代码"]),r["股票名称"],r["一级行业"],r["量化总分"],r["技术得分"],r["资金得分"],r["情绪得分"],r["动量得分"],r["风险得分"],r["进入二筛"],r["进入重点候选"]]);
const qt=tableBlock(quant,4,qHeaders,qRows,"HumanQuantTop100");
quant.getRange(`A5:L${qt.endRow}`).format.fill=C.modelSource;
quant.freezePanes.freezeRows(4); quant.getRange(`B5:B${qt.endRow}`).format.numberFormat="000000";
quant.getRange(`E5:J${qt.endRow}`).format.numberFormat="0.00"; colorScale(quant.getRange(`E5:E${qt.endRow}`));
contains(quant.getRange(`L5:L${qt.endRow}`),"是",C.green); [10,13,15,17,11,11,11,11,11,11,11,14].forEach((w,i)=>setWidth(quant,i+1,w));

// 当前问题
const issues=sheets["当前问题"];
titleBand(issues,"E","当前问题",`当前仅列出仍需处理的事项；历史已解决问题 ${data.summary["历史已解决问题数"]} 项不再重复展示。`);
const iHeaders=["股票代码","股票名称","问题类型","当前状态","说明"];
const iRows=data.issues.map((r)=>[codeCell(r["股票代码"]),r["股票名称"],r["问题类型"],r["当前状态"],r["说明"]]);
const it=tableBlock(issues,4,iHeaders,iRows,"HumanCurrentIssues");
issues.freezePanes.freezeRows(4); issues.getRange(`A5:A${it.endRow}`).format.numberFormat="000000"; issues.getRange(`E5:E${it.endRow}`).format.wrapText=true;
contains(issues.getRange(`D5:D${it.endRow}`),"已阻断",C.red); contains(issues.getRange(`D5:D${it.endRow}`),"待人工确认",C.yellow);
[14,16,18,16,52].forEach((w,i)=>setWidth(issues,i+1,w));

await fs.mkdir(path.dirname(outputPath),{recursive:true});
const output=await SpreadsheetFile.exportXlsx(workbook); await output.save(outputPath);

if(previewDir){
  await fs.mkdir(previewDir,{recursive:true});
  const ranges={"今日概览":"A1:L22","今日推荐":"A1:K16","重点候选":`A1:V${Math.max(16,ct.endRow)}`,[priceSheetName]:isMidday?"A1:R16":"A1:X16",[contextSheetName]:isMidday?"A1:L12":"A1:T12","量化前100":"A1:L18","当前问题":"A1:E12"};
  for(const [sheetName,range] of Object.entries(ranges)){
    const image=await workbook.render({sheetName,range,scale:1.1,format:"png"});
    await fs.writeFile(path.join(previewDir,`${sheetName}.png`),new Uint8Array(await image.arrayBuffer()));
  }
  const errors=await workbook.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:100},summary:"公式错误检查",maxChars:3000});
  await fs.writeFile(path.join(previewDir,"公式检查.ndjson"),errors.ndjson,"utf8");
}
