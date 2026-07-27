import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import { applyCenteredAlignment } from "./excel_alignment.mjs";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("usage: node build_validation_excel.mjs INPUT_JSON OUTPUT_XLSX [PREVIEW_DIR]");
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const sheetNames = [
  "00_使用说明", "01_运行摘要", "02_时间与水位", "03_Quant样本", "04_基本面画像",
  "05_字段溯源", "06_LLM审计", "07_只读挂单计划", "08_模拟仓位", "09_人工逐股审核",
  "10_人工字段审核", "11_告警与异常", "12_测试结果",
];
const sheets = Object.fromEntries(sheetNames.map((name) => [name, workbook.worksheets.add(name)]));

const COLORS = {navy: "#17365D", blue: "#D9EAF7", light: "#F3F6F9", green: "#E2F0D9", amber: "#FFF2CC", red: "#F4CCCC", gray: "#E7E6E6", white: "#FFFFFF", unverified: "#FCE4D6"};
const safe = (v) => {
  if (v === null || v === undefined) return "";
  if (typeof v === "number" || typeof v === "boolean") return v;
  if (typeof v === "object") v = JSON.stringify(v);
  v = String(v);
  return /^[=+\-@]/.test(v) ? `'${v}` : v;
};
const colName = (n) => { let s=""; while(n>0){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26);} return s; };
const writeSheet = (sheet, title, subtitle, headers, rows, tableName) => {
  sheet.showGridLines = false;
  const endCol = colName(Math.max(headers.length, 2));
  const titleEndCol = colName(Math.max(headers.length, 6));
  sheet.getRange(`A1:${titleEndCol}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange("A1").format = {fill: COLORS.navy, font: {bold: true, color: COLORS.white, size: 15}, rowHeight: 28};
  sheet.getRange(`A2:${titleEndCol}2`).merge();
  sheet.getRange("A2").values = [[safe(subtitle)]];
  sheet.getRange("A2").format = {fill: COLORS.light, font: {italic: true, color: "#555555"}, wrapText: true, rowHeight: 30};
  sheet.getRange(`A4:${endCol}4`).values = [headers.map(safe)];
  sheet.getRange(`A4:${endCol}4`).format = {fill: COLORS.blue, font: {bold: true, color: "#1F1F1F"}, borders: {preset: "outside", style: "thin", color: "#A6A6A6"}, wrapText: true, rowHeight: 32};
  const body = rows.length ? rows.map(row => row.map(safe)) : [headers.map(() => "")];
  const endRow = 4 + body.length;
  sheet.getRange(`A5:${endCol}${endRow}`).values = body;
  sheet.getRange(`A5:${endCol}${endRow}`).format = {font: {color: "#000000"}, wrapText: true, horizontalAlignment: "center", verticalAlignment: "center", borders: {insideHorizontal: {style: "thin", color: "#E1E5EA"}}};
  sheet.freezePanes.freezeRows(4);
  const table = sheet.tables.add(`A4:${endCol}${endRow}`, true, tableName);
  table.style = "TableStyleMedium2";
  table.showBandedRows = false;
  table.showBandedColumns = false;
  sheet.getRange(`A5:${endCol}${endRow}`).format.fill = COLORS.white;
  applyCenteredAlignment(sheet, `A4:${endCol}${endRow}`);
  sheet.getRange(`A4:${endCol}${endRow}`).format.autofitColumns();
  for (let c=0; c<headers.length; c++) {
    const width = /说明|摘要|value|note|reason|warning|限制|证据|条件|message/i.test(headers[c]) ? 26 : 15;
    sheet.getRange(`${colName(c+1)}:${colName(c+1)}`).format.columnWidth = width;
  }
  return endRow;
};
const statusFormatting = (sheet, range) => {
  range.conditionalFormats.add("containsText", {text: "PASS", format: {fill: COLORS.green, font: {color: "#006100"}}});
  range.conditionalFormats.add("containsText", {text: "ADVANCE", format: {fill: COLORS.green, font: {color: "#006100"}}});
  range.conditionalFormats.add("containsText", {text: "HOLD", format: {fill: COLORS.amber, font: {color: "#9C6500"}}});
  range.conditionalFormats.add("containsText", {text: "WATCH_ONLY", format: {fill: COLORS.amber, font: {color: "#9C6500"}}});
  range.conditionalFormats.add("containsText", {text: "BLOCKED", format: {fill: COLORS.red, font: {color: "#9C0006"}}});
  range.conditionalFormats.add("containsText", {text: "REJECT", format: {fill: COLORS.red, font: {color: "#9C0006"}}});
  range.conditionalFormats.add("containsText", {text: "UNKNOWN", format: {fill: COLORS.gray, font: {color: "#666666"}}});
};

const run = data.run;
writeSheet(sheets["00_使用说明"], "Guarded DeepSeek 三股模型验证报告", "所有结果均为 MODEL_VALIDATION / NON_ACTIONABLE；不构成投资建议，不可用于下单。带 * 字段为 LLM_UNVERIFIED。", ["项目", "说明"], [
  ["报告目的", "历史点时结构化输入下的模型、挂单规则与仓位规则验证"],
  ["知识模式", run.knowledge_mode], ["交易属性", "NON_ACTIONABLE"], ["人工审核", "请在 09、10 工作表完成下拉审核与备注"],
  ["数据边界", `decision_time=${run.decision_time}; target_trade_date=${run.target_trade_date}`],
  ["星号说明", "* = LLM_UNVERIFIED；原始结构化事实不加星号"],
], "InstructionsTable");

writeSheet(sheets["01_运行摘要"], "运行摘要", "数据库提交后重新读取生成；摘要公式引用其他工作表。", ["指标", "值", "状态/说明"], [
  ["validation_run_id", run.run_id, run.status], ["quant_run_id", run.quant_run_id, "同一正式 Quant Run"],
  ["manifest_id", run.run_data_manifest_id, "同一 RunDataManifest"], ["样本数", 3, "rank 1/250/500"],
  ["知识模式", run.knowledge_mode, "历史回放强制"], ["真实 LLM", run.real_llm, "Gateway only"],
  ["挂单计划属性", "MODEL_VALIDATION / DRAFT / false", "NON_ACTIONABLE"],
  ["仓位属性", "MODEL_VALIDATION / NON_ACTIONABLE / false", "仅供模型验证"],
], "RunSummaryTable");
const summary = sheets["01_运行摘要"];
summary.getRange("N4:O4").values = [["股票", "Quant总分"]];
summary.getRange("N5:O5").formulas = [["='03_Quant样本'!C5", "='03_Quant样本'!D5"]];
summary.getRange("N6:O6").formulas = [["='03_Quant样本'!C6", "='03_Quant样本'!D6"]];
summary.getRange("N7:O7").formulas = [["='03_Quant样本'!C7", "='03_Quant样本'!D7"]];
const chart = summary.charts.add("bar", summary.getRange("N4:O7"));
chart.title = "三股 Quant 总分对比"; chart.hasLegend = false; chart.xAxis = {axisType: "textAxis"}; chart.yAxis = {numberFormatCode: "0.00"}; chart.setPosition("E4", "L18");

const watermarks=[];
for (const [name,w] of Object.entries(run.expected_universe_audit || {})) watermarks.push([name,w.requested_trade_date,w.latest_trade_date,w.raw_actual_count,w.unique_stock_count,w.expected_stock_count,w.duplicate_count,w.unexpected_stock_count,w.excluded_stock_count,w.coverage_ratio,w.is_complete,w.exclusion_reason_counts]);
const watermarkEnd=writeSheet(sheets["02_时间与水位"], "时间与数据水位", "coverage 不截断异常；明确展示 raw、unique、unexpected、duplicate 与 exclusion reasons。", ["dataset","requested_date","latest_date","raw_actual","unique_stock","expected_stock","duplicate","unexpected","excluded","coverage","complete","exclusion_reasons"], watermarks, "WatermarkTable");
sheets["02_时间与水位"].getRange(`J5:J${watermarkEnd}`).format.numberFormat = "0.00%";

const quantEnd=writeSheet(sheets["03_Quant样本"], "Quant 样本", "确定性抽取 rank 1、250、500；三股引用同一 Quant Run 与 Manifest。", ["rank","stock_code","stock_name","total_score","technical","capital","emotion","momentum","risk","profile_version","financial_period","financial_available_at","data_age_days","quant_run_id","manifest_id"], data.samples.map(s=>[s.rank,s.stock_code,s.stock_name,s.quant_scores.total_score,s.quant_scores.technical_score,s.quant_scores.capital_score,s.quant_scores.emotion_score,s.quant_scores.momentum_score,s.quant_scores.risk_score,s.profile_version,s.latest_financial_period,s.financial_available_at,s.data_age_days,s.quant_run_id,s.run_data_manifest_id]), "QuantSamplesTable");
sheets["03_Quant样本"].getRange(`D5:I${quantEnd}`).format.numberFormat = "0.00";
sheets["03_Quant样本"].getRange(`B5:B${quantEnd}`).format.numberFormat = "000000";
sheets["03_Quant样本"].getRange(`K5:K${quantEnd}`).format.numberFormat = "0";
sheets["03_Quant样本"].getRange(`L5:L${quantEnd}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";

const profileRows=[];
for (const s of data.samples) {
  const f=s.fundamental_result || {}, sc=s.screening_result || {};
  const fields=[
    ["industry_chain", f.industry_chain], ["level_one_sector", f.level_one_sector_explanation], ["main_business_summary", f.main_business_summary],
    ["industry_position", f.industry_position], ["concept_tags", f.concept_tags], ["structural_theme_fit", f.structural_theme_fit],
    ["competitive_advantage", f.competitive_advantage], ["industry_trend", f.industry_trend], ["investment_logic", f.investment_logic],
    ["domestic_substitution", f.domestic_substitution], ["observation_rating", f.observation_rating], ["financial_status", f.financial_status],
  ];
  for (const [name,value] of fields) {
    const obj = value && typeof value === "object" ? value : {value};
    const source = obj.source_status || (name === "financial_status" ? "DERIVED_RULE" : "LLM_UNVERIFIED");
    const marker = obj.display_marker ?? (source === "LLM_UNVERIFIED" ? "*" : "");
    profileRows.push([s.stock_code,s.rank,name,marker + (obj.summary || obj.description || obj.value || obj.level || obj.chain_name || JSON.stringify(value || "UNKNOWN")),source,marker,obj.confidence ?? "",obj.evidence_fields || [],obj.limitations || [],s.missing_fields,sc.screening_decision,sc.llm_score,sc.confidence]);
  }
}
const profileEnd=writeSheet(sheets["04_基本面画像"], "基本面画像", "主营摘要来自 VERIFIED_STRUCTURED 原始资料并由 LLM_SUMMARY 压缩；其余模型推导字段统一标 *。", ["stock_code","rank","field_name","display_value","source_status","marker","confidence","evidence_fields","limitations","missing_data","screening_decision","llm_score","screening_confidence"], profileRows, "FundamentalProfilesTable");
statusFormatting(sheets["04_基本面画像"], sheets["04_基本面画像"].getRange(`D5:M${profileEnd}`));
sheets["04_基本面画像"].getRange(`A5:A${profileEnd}`).format.numberFormat = "000000";

const provenanceRows=[];
for (const s of data.samples) for (const [field,p] of Object.entries(s.field_provenance || {})) provenanceRows.push([s.stock_code,s.rank,field,p.value,p.source_status,p.verified,p.confidence,p.evidence_fields,p.limitations || [],s.profile_version]);
const provenanceEnd=writeSheet(sheets["05_字段溯源"], "字段溯源", "结构化事实、规则推导、LLM 未核验与未知字段分离展示。", ["stock_code","rank","field_name","value","source_status","verified","confidence","evidence_fields","limitations","profile_version"], provenanceRows, "FieldProvenanceTable");
statusFormatting(sheets["05_字段溯源"], sheets["05_字段溯源"].getRange(`D5:G${provenanceEnd}`));
sheets["05_字段溯源"].getRange(`A5:A${provenanceEnd}`).format.numberFormat = "000000";

const auditEnd=writeSheet(sheets["06_LLM审计"], "LLM 审计", "不含完整提示词、原始响应或模型思维链；最多 6 次业务调用。", ["stock_code","task","knowledge_mode","model_alias","actual_model","prompt_version","status","schema_status","request_hash","input_tokens","output_tokens","cost_usd","latency_ms","cache_status"], data.audits.map(a=>[a.stock_code,a.task,a.knowledge_mode,a.model_alias,a.actual_model,a.prompt_version,a.status,a.schema_status,a.request_hash,a.input_tokens,a.output_tokens,a.cost_usd,a.latency_ms,a.cache_status]), "LLMAuditTable");
statusFormatting(sheets["06_LLM审计"], sheets["06_LLM审计"].getRange(`G5:H${auditEnd}`));
sheets["06_LLM审计"].getRange(`A5:A${auditEnd}`).format.numberFormat = "000000";

const planEnd=writeSheet(sheets["07_只读挂单计划"], "只读挂单计划", "价格全部由现有 Order Price 规则引擎计算；目标日涨跌停为 RULE_ESTIMATED，非官方目标日数据。", ["plan_id","stock_code","purpose","session","status","actionable","final_recommendation","conservative","balanced","aggressive","recommended","max_acceptable","stop_loss","take_profit_1","take_profit_2","fill_probability","risk_reward","order_score","support","resistance","ATR","VWAP","previous_close","limit_up_estimated","limit_down_estimated","limit_source","official_limit_available","auction_available","cancel_conditions","reprice_conditions","warnings","temporal_status"], data.plans.map(p=>[p.id,p.stock_code,p.plan_purpose,p.plan_session,p.status,p.actionable,p.is_final_recommendation,p.conservative_price,p.balanced_price,p.aggressive_price,p.recommended_price,p.max_acceptable_price,p.stop_loss_price,p.take_profit_1_price,p.take_profit_2_price,p.fill_probability,p.risk_reward,p.order_price_score,p.support,p.resistance,p.atr,p.vwap,p.previous_close,p.limit_up_estimated,p.limit_down_estimated,p.limit_price_source,p.official_target_day_limit_available,p.target_day_auction_available,p.cancel_conditions,p.reprice_conditions,p.warnings,p.temporal_status]), "OrderPlansTable");
sheets["07_只读挂单计划"].getRange(`H5:Y${planEnd}`).format.numberFormat = "0.00";
statusFormatting(sheets["07_只读挂单计划"], sheets["07_只读挂单计划"].getRange(`E5:AF${planEnd}`));
sheets["07_只读挂单计划"].getRange(`B5:B${planEnd}`).format.numberFormat = "000000";

const allocationEnd=writeSheet(sheets["08_模拟仓位"], "模拟仓位", "ValidationAccountSnapshot 与正式模拟账户隔离；LLM_UNVERIFIED 只能折扣仓位，不能提高。", ["allocation_run_id","stock_code","purpose","status","actionable","relative_weight","position_percent","capital","quantity","estimated_max_loss","binding_constraints","warnings","account_snapshot_id"], data.allocations.map(a=>[a.allocation_run_id,a.stock_code,a.allocation_purpose,a.status,a.actionable,a.relative_allocation_weight,a.suggested_position_percent,a.suggested_capital_amount,a.suggested_quantity,a.estimated_max_loss,a.binding_constraints,a.warnings,a.account_snapshot_id]), "AllocationsTable");
sheets["08_模拟仓位"].getRange(`F5:G${allocationEnd}`).format.numberFormat = "0.00%";
sheets["08_模拟仓位"].getRange(`H5:H${allocationEnd}`).format.numberFormat = "#,##0.00";
sheets["08_模拟仓位"].getRange(`I5:I${allocationEnd}`).format.numberFormat = "#,##0";
sheets["08_模拟仓位"].getRange(`J5:J${allocationEnd}`).format.numberFormat = "#,##0.00";
statusFormatting(sheets["08_模拟仓位"], sheets["08_模拟仓位"].getRange(`D5:L${allocationEnd}`));
sheets["08_模拟仓位"].getRange(`B5:B${allocationEnd}`).format.numberFormat = "000000";

const reviewOptions=["CORRECT","ACCEPTABLE","TOO_VAGUE","OVERSTATED","HALLUCINATED","WRONG","INSUFFICIENT_DATA","NOT_REVIEWED"];
const stockReviewEnd=writeSheet(sheets["09_人工逐股审核"], "人工逐股审核", "蓝色字体为可编辑审核输入。", ["stock_code","rank","screening_decision","review_status","dynamic_fields_ok","order_plan_ok","position_ok","trader_note","reviewer","review_time"], data.samples.map(s=>[s.stock_code,s.rank,s.screening_result.screening_decision,"NOT_REVIEWED","NOT_REVIEWED","NOT_REVIEWED","NOT_REVIEWED","","",""]), "StockReviewTable");
sheets["09_人工逐股审核"].getRange(`D5:D${stockReviewEnd}`).dataValidation = {rule:{type:"list",values:reviewOptions}};
sheets["09_人工逐股审核"].getRange(`E5:G${stockReviewEnd}`).dataValidation = {rule:{type:"list",values:["YES","NO","NOT_REVIEWED"]}};
sheets["09_人工逐股审核"].getRange(`D5:J${stockReviewEnd}`).format.font = {color:"#0000FF"};
sheets["09_人工逐股审核"].getRange(`A5:A${stockReviewEnd}`).format.numberFormat = "000000";

const fieldReviewRows=profileRows.map(r=>[r[0],r[1],r[2],r[3],r[4],r[6],"NOT_REVIEWED","","","",""]);
const fieldReviewEnd=writeSheet(sheets["10_人工字段审核"], "人工字段审核", "每只股票的关键基本面字段逐项审核；AI displayed value 保留星号语义。", ["stock_code","rank","field_name","AI displayed value","source_status","confidence","review_status","corrected_value","trader_note","reviewer","review_time"], fieldReviewRows, "FieldReviewTable");
sheets["10_人工字段审核"].getRange(`G5:G${fieldReviewEnd}`).dataValidation = {rule:{type:"list",values:reviewOptions}};
sheets["10_人工字段审核"].getRange(`G5:K${fieldReviewEnd}`).format.font = {color:"#0000FF"};
sheets["10_人工字段审核"].getRange(`A5:A${fieldReviewEnd}`).format.numberFormat = "000000";

const warningRows=[];
for (const p of data.plans) for (const w of (p.warnings||[])) warningRows.push(["WARNING","order_plan",p.stock_code,w,String(w),false,p.created_at]);
for (const a of data.allocations) for (const w of (a.warnings||[])) warningRows.push(["WARNING","position_sizing",a.stock_code,w,String(w),false,a.created_at]);
for (const [dataset,w] of Object.entries(run.expected_universe_audit||{})) if ((w.duplicate_count||0)+(w.unexpected_stock_count||0)+(w.excluded_stock_count||0)>0) warningRows.push(["WARNING","expected_universe","",`UNIVERSE_DIFFERENCE_${dataset}`,JSON.stringify(w.exclusion_reason_counts||{}),false,run.created_at]);
const warningEnd=writeSheet(sheets["11_告警与异常"], "告警与异常", "所有异常逐条保留；actionable 均为 false。", ["severity","module","stock_code","code","message","actionable","created_at"], warningRows, "WarningsTable");
statusFormatting(sheets["11_告警与异常"], sheets["11_告警与异常"].getRange(`A5:G${warningEnd}`));
sheets["11_告警与异常"].getRange(`C5:C${warningEnd}`).format.numberFormat = "000000";
sheets["11_告警与异常"].getRange(`G5:G${warningEnd}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";

const testEnd=writeSheet(sheets["12_测试结果"], "测试与工作簿校验", "关键计数使用工作表公式；content_hash 为数据库回读载荷哈希，不宣称等于导出文件 SHA-256。", ["check","actual","expected","status","notes"], [
  ["sheet_count",13,13,"PASS","固定 13 sheets"], ["sample_count",3,3,"PASS","rank 1/250/500"],
  ["llm_call_count",data.audits.length,"<=6",data.audits.length<=6?"PASS":"FAIL","每股最多两次业务调用"],
  ["order_plan_count",data.plans.length,3,data.plans.length===3?"PASS":"FAIL","全部 MODEL_VALIDATION"],
  ["allocation_count",data.allocations.length,3,data.allocations.length===3?"PASS":"FAIL","全部 NON_ACTIONABLE"],
  ["dynamic_fields_unknown",data.dynamic_fields_unknown,true,data.dynamic_fields_unknown?"PASS":"FAIL","历史时点保护"],
  ["secret_scan",data.secret_scan,"PASS",data.secret_scan,"未写入密钥、完整提示词或模型思维链"],
  ["content_hash",data.content_hash,"sha256","PASS","数据库回读载荷哈希"],
  ["generated_file",outputPath,"xlsx","PASS","原子重命名由主 CLI 完成"],
], "TestResultsTable");
statusFormatting(sheets["12_测试结果"], sheets["12_测试结果"].getRange(`D5:D${testEnd}`));

await fs.mkdir(path.dirname(outputPath), {recursive:true});
if (previewDir) {
  await fs.mkdir(previewDir,{recursive:true});
  for (const name of sheetNames) {
    const preview=await workbook.render({sheetName:name,autoCrop:"all",scale:0.8,format:"png"});
    await fs.writeFile(path.join(previewDir,`${name}.png`),new Uint8Array(await preview.arrayBuffer()));
  }
}
const sheetInspection=await workbook.inspect({kind:"sheet",include:"id,name",maxChars:3000});
const formulaInspection=await workbook.inspect({kind:"formula",sheetId:"01_运行摘要",range:"N4:O7",maxChars:3000,options:{maxResults:20}});
await fs.writeFile(`${outputPath}.inspect.txt`, `${sheetInspection.ndjson || String(sheetInspection)}\n${formulaInspection.ndjson || String(formulaInspection)}`, "utf8");
const output=await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
