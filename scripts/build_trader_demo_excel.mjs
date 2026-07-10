import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("usage: node build_trader_demo_excel.mjs INPUT_JSON OUTPUT_XLSX [PREVIEW_DIR]");
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const names = ["01_全A量化排名", "02_Top100_LLM评分", "03_挂单与仓位", "04_重点基本面", "05_说明与异常"];
const sheets = Object.fromEntries(names.map((name) => [name, workbook.worksheets.add(name)]));

const C = {
  navy: "#17365D", navy2: "#244A73", white: "#FFFFFF", light: "#F4F7FA",
  green: "#E2F0D9", greenText: "#006100", blue: "#D9EAF7", blueText: "#1F4E78",
  purple: "#E4DFEC", purpleText: "#5F497A", yellow: "#FFF2CC", amberText: "#9C6500",
  red: "#F4CCCC", redText: "#9C0006", gray: "#E7E6E6", grayText: "#666666",
  unverified: "#FFF2CC", border: "#D9E1E8",
};
const col = (n) => { let s = ""; while (n > 0) { n--; s = String.fromCharCode(65 + n % 26) + s; n = Math.floor(n / 26); } return s; };
const safe = (value) => {
  if (value === null || value === undefined) return "";
  if (typeof value === "number" || typeof value === "boolean") return value;
  const text = String(value);
  return /^[=+\-@]/.test(text) ? `'${text}` : text;
};
const setWidth = (sheet, index, width) => sheet.getRange(`${col(index)}:${col(index)}`).format.columnWidth = width;
const tableBlock = (sheet, startRow, headers, rows, tableName) => {
  const endCol = col(headers.length);
  const body = rows.length ? rows.map((row) => row.map(safe)) : [headers.map(() => "")];
  const endRow = startRow + body.length;
  sheet.getRange(`A${startRow}:${endCol}${startRow}`).values = [headers];
  sheet.getRange(`A${startRow}:${endCol}${startRow}`).format = {
    fill: C.navy, font: {bold: true, color: C.white, size: 10}, wrapText: true,
    verticalAlignment: "center", rowHeight: 34,
  };
  sheet.getRange(`A${startRow + 1}:${endCol}${endRow}`).values = body;
  sheet.getRange(`A${startRow + 1}:${endCol}${endRow}`).format = {
    font: {color: "#202020", size: 9}, verticalAlignment: "top",
    borders: {insideHorizontal: {style: "thin", color: C.border}},
  };
  const table = sheet.tables.add(`A${startRow}:${endCol}${endRow}`, true, tableName);
  table.style = "TableStyleMedium2";
  table.showFilterButton = true;
  return {endRow, bodyStart: startRow + 1, endCol};
};
const colorScale = (range) => range.conditionalFormats.add("colorScale", {
  criteria: [
    {type: "lowestValue", color: "#F8696B"},
    {type: "percentile", value: 50, color: "#FFEB84"},
    {type: "highestValue", color: "#63BE7B"},
  ],
});
const contains = (range, text, fill, font) => range.conditionalFormats.add("containsText", {text, format: {fill, font: {color: font, bold: true}}});
const sourceFormats = (range) => {
  contains(range, "LLM", C.green, C.greenText);
  contains(range, "MANUAL", C.blue, C.blueText);
  contains(range, "BOTH", C.purple, C.purpleText);
};
for (const sheet of Object.values(sheets)) {
  sheet.showGridLines = false;
}

// 01 - all scored A-share quant rows.
const qHeaders = ["量化排名","股票代码","股票名称","交易所","一级板块","行业分类口径","量化总分","技术得分","资金得分","情绪得分","动量得分","风险得分","涨跌停状态","数据覆盖状态","是否Quant Top100","是否进入Flash分析","是否Top20入选","是否人工选择","交易候选来源","Quant Run ID"];
const qRows = data.quant_rows.map((r) => [r.rank,r.stock_code,r.stock_name,r.exchange,r.level_one_sector,r.classification_standard,r.total_score,r.technical_score,r.capital_score,r.emotion_score,r.momentum_score,r.risk_score,r.limit_status,r.data_coverage_status,r.quant_top100?"是":"否",r.llm_evaluated?"是":"否",r.llm_selected?"是":"否",r.manual_selected?"是":"否",r.selection_source,r.quant_run_id]);
const q = tableBlock(sheets[names[0]], 1, qHeaders, qRows, "AllAQuantRankingTable");
sheets[names[0]].freezePanes.freezeRows(1);
sheets[names[0]].getRange(`B2:B${q.endRow}`).format.numberFormat = "@";
sheets[names[0]].getRange(`G2:L${q.endRow}`).format.numberFormat = "0.00";
colorScale(sheets[names[0]].getRange(`G2:G${q.endRow}`));
const quantBody = sheets[names[0]].getRange(`A2:T${q.endRow}`);
quantBody.conditionalFormats.addCustom('=$O2="是"',{fill:"#FFF9E6"});
quantBody.conditionalFormats.addCustom('=$S2="LLM"',{fill:C.green});
quantBody.conditionalFormats.addCustom('=$S2="MANUAL"',{fill:C.blue});
quantBody.conditionalFormats.addCustom('=$S2="BOTH"',{fill:C.purple});
contains(sheets[names[0]].getRange(`O2:O${q.endRow}`), "是", C.yellow, C.amberText);
contains(sheets[names[0]].getRange(`Q2:Q${q.endRow}`), "是", C.green, C.greenText);
contains(sheets[names[0]].getRange(`R2:R${q.endRow}`), "是", C.blue, C.blueText);
sourceFormats(sheets[names[0]].getRange(`S2:S${q.endRow}`));
[9,12,13,8,18,20,11,11,11,11,11,11,13,13,13,14,12,12,13,30].forEach((w,i)=>setWidth(sheets[names[0]],i+1,w));

// 02 - every stock actually in the LLM evaluation pool.
const lHeaders = ["Flash评估序号","股票代码","股票名称","量化排名","量化总分","Flash评分","二筛结论","Flash置信度","量化一致性分","基本面质量分","财务质量分","风险适配分","数据质量评分","数据质量扣分","风险扣分","Flash版本","基本面信号","量化一致性信号","财务信号","数据冲突","是否Top20入选","是否人工选择","是否进入交易候选","入选来源","核心判断理由","风险提示","缺失信息","Flash执行状态","错误类别","Pro评分","Pro排名","Pro优先级","Pro摘要"];
const lRows = data.llm_rows.map((r,i)=>[i+1,r.stock_code,r.stock_name,r.quant_rank,r.quant_score,r.llm_score,r.decision,r.confidence,r.quant_consistency_score,r.fundamental_quality_score,r.financial_quality_score,r.risk_fit_score,r.data_quality_score,r.data_quality_penalty,r.risk_penalty,r.flash_score_version,r.fundamental_signal,r.quant_consistency_signal,r.financial_signal,r.data_conflict?"是":"否",r.llm_selected?"是":"否",r.manual_selected?"是":"否",r.trading_candidate?"是":"否",r.selection_source,r.reason,r.risk_note,r.missing_information,r.execution_status,r.error_category,r.pro_score,r.pro_rank,r.pro_priority,r.pro_summary]);
const l = tableBlock(sheets[names[1]], 1, lHeaders, lRows, "LLMScreeningScoresTable");
sheets[names[1]].freezePanes.freezeRows(1);
sheets[names[1]].getRange(`B2:B${l.endRow}`).format.numberFormat = "@";
sheets[names[1]].getRange(`E2:F${l.endRow}`).format.numberFormat = "0.00";
sheets[names[1]].getRange(`H2:H${l.endRow}`).format.numberFormat = "0.00%";
sheets[names[1]].getRange(`I2:O${l.endRow}`).format.numberFormat = "0.00";
colorScale(sheets[names[1]].getRange(`E2:F${l.endRow}`));
contains(sheets[names[1]].getRange(`G2:G${l.endRow}`), "ADVANCE", C.green, C.greenText);
contains(sheets[names[1]].getRange(`G2:G${l.endRow}`), "HOLD", C.yellow, C.amberText);
contains(sheets[names[1]].getRange(`G2:G${l.endRow}`), "WATCH_ONLY", C.yellow, C.amberText);
contains(sheets[names[1]].getRange(`G2:G${l.endRow}`), "REJECT", C.red, C.redText);
contains(sheets[names[1]].getRange(`G2:G${l.endRow}`), "SCHEMA_ERROR", C.red, C.redText);
contains(sheets[names[1]].getRange(`AB2:AC${l.endRow}`), "FAILED", C.gray, C.grayText);
sourceFormats(sheets[names[1]].getRange(`X2:X${l.endRow}`));
sheets[names[1]].getRange(`Y2:AA${l.endRow}`).format.wrapText = true;
for (let i=1;i<=33;i++) setWidth(sheets[names[1]],i,[3,25,26,27,33].includes(i)?28:12);

// 03 - default trader working sheet.
const orderSheet = sheets[names[2]];
orderSheet.getRange("A1:AP1").merge();
orderSheet.getRange("A1").values = [["当前为MODEL_VALIDATION，仅供调试，不构成交易建议。"]];
orderSheet.getRange("A1").format = {fill:C.red,font:{bold:true,color:C.redText,size:12},rowHeight:26};
orderSheet.getRange("A2:B2").values = [["交易候选数",data.order_rows.length]];
if (data.order_rows.length === 0) {
  orderSheet.getRange("A3:AP3").merge();
  orderSheet.getRange("A3").values = [["本次无交易候选：LLM入选0只，人工选择0只。"]];
  orderSheet.getRange("A3").format = {fill:C.gray,font:{italic:true,color:C.grayText}};
}
orderSheet.getRange("D2:E2").values = [["建议总仓位",null]];
orderSheet.getRange("G2:H2").values = [["可用资金",data.account.available_cash || 0]];
orderSheet.getRange("J2:K2").values = [["建议总资金",null]];
orderSheet.getRange("M2:N2").values = [["预计最大损失",null]];
orderSheet.getRange("P2:Q2").values = [["剩余可部署资金",null]];
for (const range of ["A2:B2","D2:E2","G2:H2","J2:K2","M2:N2","P2:Q2"]) orderSheet.getRange(range).format = {fill:C.light,font:{bold:true,color:C.navy},borders:{preset:"outside",style:"thin",color:C.border}};
const oHeaders = ["股票代码","股票名称","选择来源","人工选择理由","量化排名","量化总分","LLM评分","LLM结论","基本面观察评级","风险等级","保守价","均衡价","激进价","推荐挂单价","最高可接受价","止损价","止盈1","止盈2","风险收益比(TP1)","风险收益比(TP2)","当前使用的风险收益目标","当前有效风险收益比","成交概率","挂单计划状态","挂单警告","候选池相对权重","建议账户仓位","建议资金金额","建议股数","预计最大损失","单笔风险预算","仓位状态","主要约束","仓位警告","一级板块","产业链环节","主营摘要","财务状态","是否可执行","Pro评分","Pro排名","Pro优先级"];
const oRows = data.order_rows.map((r)=>[r.stock_code,r.stock_name,r.selection_source,r.manual_reason,r.quant_rank,r.quant_score,r.llm_score,r.llm_decision,r.observation_rating,r.risk_level,r.conservative_price,r.balanced_price,r.aggressive_price,r.recommended_price,r.max_acceptable_price,r.stop_loss_price,r.take_profit_1,r.take_profit_2,r.risk_reward_to_tp1,r.risk_reward_to_tp2,r.active_target_mode,r.active_risk_reward,r.fill_probability,r.order_status,r.order_warning,r.relative_weight,r.position_percent,r.capital_amount,r.quantity,r.max_loss,r.risk_budget,r.position_status,r.binding_constraint,r.position_warning,r.level_one_sector,r.chain_position,r.main_business,r.financial_status,r.actionable,r.pro_score,r.pro_rank,r.pro_priority]);
const o = tableBlock(orderSheet, 4, oHeaders, oRows, "TradingCandidatePlanTable");
orderSheet.freezePanes.freezeRows(4);
orderSheet.getRange(`A5:A${o.endRow}`).format.numberFormat = "@";
orderSheet.getRange(`F5:G${o.endRow}`).format.numberFormat = "0.00";
orderSheet.getRange(`K5:R${o.endRow}`).format.numberFormat = "0.00";
orderSheet.getRange(`S5:T${o.endRow}`).format.numberFormat = "0.00";
orderSheet.getRange(`V5:W${o.endRow}`).format.numberFormat = "0.00";
orderSheet.getRange(`Z5:AA${o.endRow}`).format.numberFormat = "0.00%";
orderSheet.getRange(`AB5:AB${o.endRow}`).format.numberFormat = "#,##0.00";
orderSheet.getRange(`AC5:AC${o.endRow}`).format.numberFormat = "#,##0";
orderSheet.getRange(`AD5:AE${o.endRow}`).format.numberFormat = "#,##0.00";
sourceFormats(orderSheet.getRange(`C5:C${o.endRow}`));
contains(orderSheet.getRange(`X5:AH${o.endRow}`), "BLOCKED", C.red, C.redText);
contains(orderSheet.getRange(`X5:AH${o.endRow}`), "NON_ACTIONABLE", C.red, C.redText);
contains(orderSheet.getRange(`H5:J${o.endRow}`), "REJECT", C.red, C.redText);
contains(orderSheet.getRange(`H5:J${o.endRow}`), "INSUFFICIENT_DATA", C.gray, C.grayText);
orderSheet.getRange(`D5:D${o.endRow}`).format.wrapText = true;
orderSheet.getRange(`Y5:AH${o.endRow}`).format.wrapText = true;
const totalRow = o.endRow + 2;
orderSheet.getRange(`Z${totalRow}:AE${totalRow}`).values = [["合计",null,null,null,null,null]];
orderSheet.getRange(`AA${totalRow}`).formulas = [[`=SUM(AA5:AA${o.endRow})`]];
orderSheet.getRange(`AB${totalRow}`).formulas = [[`=SUM(AB5:AB${o.endRow})`]];
orderSheet.getRange(`AC${totalRow}`).formulas = [[`=SUM(AC5:AC${o.endRow})`]];
orderSheet.getRange(`AD${totalRow}`).formulas = [[`=SUM(AD5:AD${o.endRow})`]];
orderSheet.getRange(`Z${totalRow}:AE${totalRow}`).format = {fill:C.navy,font:{bold:true,color:C.white},borders:{preset:"outside",style:"thin",color:C.navy}};
orderSheet.getRange(`AA${totalRow}`).format.numberFormat = "0.00%";
orderSheet.getRange(`AB${totalRow}:AD${totalRow}`).format.numberFormat = "#,##0.00";
orderSheet.getRange("E2").formulas = [[`=SUM(AA5:AA${o.endRow})`]];
orderSheet.getRange("K2").formulas = [[`=SUM(AB5:AB${o.endRow})`]];
orderSheet.getRange("N2").formulas = [[`=SUM(AD5:AD${o.endRow})`]];
orderSheet.getRange("Q2").formulas = [[`=H2-SUM(AB5:AB${o.endRow})`]];
orderSheet.getRange("E2").format.numberFormat = "0.00%";
orderSheet.getRange("H2:Q2").format.numberFormat = "#,##0.00";
for (let i=1;i<=42;i++) setWidth(orderSheet,i,[4,25,34,37].includes(i)?26:12);

// 04 - flattened fundamentals for the complete LLM evaluation pool.
const fSheet = sheets[names[3]];
fSheet.getRange("A1:BD1").merge();
fSheet.getRange("A1").values = [["* 表示DeepSeek基于结构化数据形成的未外部核验判断。"]];
fSheet.getRange("A1").format = {fill:C.unverified,font:{bold:true,color:C.amberText},rowHeight:24};
fSheet.getRange("A2:BD2").merge();
fSheet.getRange("A2").values = [["财务金额单位：万元；百分比列使用百分比格式。"]];
fSheet.getRange("A2").format = {fill:C.light,font:{italic:true,color:C.grayText}};
const fHeaders = ["股票代码","股票名称","选择来源","量化排名","LLM评分","产业链归属","产业链环节","一级板块","分类口径","主营业务","核心产品","主营构成","行业地位","概念标签","结构性题材匹配","竞争优势","行业趋势","投资逻辑","逻辑失效条件","国产替代","基本面观察评级","财务状态","财务状态说明","财务报告期","营业收入(万元)","营收同比","归母净利润(万元)","归母净利润同比","扣非归母净利润(万元)","扣非净利润同比","毛利率","净利率","经营现金流(万元)","资产负债率","货币资金(万元)","交易性金融资产(万元)","总资产(万元)","总负债(万元)","未核验字段","缺失字段","数据冲突","基本面置信度","需要人工复核","基本面执行状态","基本面错误类别","输入资料状态","是否LLM入选","是否人工选择","是否交易候选","LLM执行状态","Pro高级摘要","Pro优势","Pro风险","Pro基本面质量","Pro量化一致性","Pro人工复核优先级"];
const fRows = data.fundamental_rows.map((r)=>[r.stock_code,r.stock_name,r.selection_source,r.quant_rank,r.llm_score,r.industry_chain,r.chain_position,r.level_one_sector,r.classification_standard,r.main_business,r.core_products,r.main_business_composition,r.industry_position,r.concept_tags,r.structural_theme_fit,r.competitive_advantage,r.industry_trend,r.investment_logic,r.logic_invalidation,r.domestic_substitution,r.observation_rating,r.financial_status,r.financial_status_reason,r.financial_period,r.revenue,r.revenue_yoy,r.net_profit,r.net_profit_yoy,r.deducted_net_profit,r.deducted_profit_yoy,r.gross_margin,r.net_margin,r.operating_cash_flow,r.debt_ratio,r.cash,r.trading_financial_assets,r.total_assets,r.total_liabilities,r.unverified_fields,r.missing_fields,r.data_conflict,r.fundamental_confidence,r.manual_review,r.fundamental_execution_status,r.fundamental_error_category,r.input_profile_status,r.llm_selected,r.manual_selected,r.trading_candidate,r.llm_execution_status,r.pro_summary,r.pro_strengths,r.pro_risks,r.pro_fundamental_quality,r.pro_quant_consistency,r.pro_manual_review_priority]);
const f = tableBlock(fSheet, 4, fHeaders, fRows, "TradingCandidateFundamentalsTable");
fSheet.freezePanes.freezeRows(4);
fSheet.getRange(`A5:A${f.endRow}`).format.numberFormat = "@";
fSheet.getRange(`E5:E${f.endRow}`).format.numberFormat = "0.00";
for (const c of [26,28,30,31,32,34,42]) fSheet.getRange(`${col(c)}5:${col(c)}${f.endRow}`).format.numberFormat = "0.00%";
for (const c of [25,27,29,33,35,36,37,38]) fSheet.getRange(`${col(c)}5:${col(c)}${f.endRow}`).format.numberFormat = "#,##0.00";
sourceFormats(fSheet.getRange(`C5:C${f.endRow}`));
contains(fSheet.getRange(`F5:BD${f.endRow}`), "*", C.unverified, C.amberText);
contains(fSheet.getRange(`T5:V${f.endRow}`), "INSUFFICIENT_DATA", C.gray, C.grayText);
fSheet.getRange(`F5:BD${f.endRow}`).format.wrapText = true;
for (let i=1;i<=56;i++) setWidth(fSheet,i,[1,2,3,4,5,21,22,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,41,42,43,44,45,46,47,48,49,50,54,55,56].includes(i)?12:24);
setWidth(fSheet,1,12); setWidth(fSheet,2,14); setWidth(fSheet,3,12); setWidth(fSheet,4,10); setWidth(fSheet,5,11);

// 05 - compact operational metadata and exceptions.
const info = sheets[names[4]];
info.getRange("A1:G1").merge();
info.getRange("A1").values = [["运行说明"]];
info.getRange("A1").format = {fill:C.navy,font:{bold:true,color:C.white,size:13},rowHeight:26};
const totalInput = data.audits.reduce((sum,row)=>sum+(row.input_tokens||0),0);
const totalOutput = data.audits.reduce((sum,row)=>sum+(row.output_tokens||0),0);
const totalCost = data.audits.reduce((sum,row)=>sum+(row.cost_usd||0),0);
const successCalls = data.audits.filter((row)=>row.schema_status==="PASS").length;
const failedCalls = data.audits.length-successCalls;
const meta = [
  ["文件生成时间",data.generated_at], ["Quant Run ID",data.run.quant_run_id], ["Manifest ID",data.run.run_data_manifest_id],
  ["基础交易日",data.run.base_market_trade_date], ["目标交易日",data.run.target_trade_date], ["Quant股票数",data.row_counts.quant_scored_count],
  ["LLM评估股票数",data.row_counts.llm_evaluation_count], ["LLM入选数",data.row_counts.llm_selected_count], ["人工选择数",data.row_counts.manual_selected_count],
  ["LLM ADVANCE数",data.row_counts.llm_advance_count], ["LLM成功股票数",data.row_counts.llm_success_count], ["LLM失败股票数",data.row_counts.llm_failure_count],
  ["Trading Candidate数",data.row_counts.trading_candidate_count], ["工作簿状态",data.run.status], ["DeepSeek模型",data.audits.find((r)=>r.actual_model)?.actual_model || "deepseek-v4-flash"],
  ["knowledge mode",data.run.knowledge_mode], ["账户权益",data.account.account_equity || 0], ["可用资金",data.account.available_cash || 0],
  ["说明1","Quant分数和LLM分数不可人工修改；人工只决定是否加入候选池。"], ["说明2","挂单价格与仓位均由规则引擎计算。"],
  ["说明3","* 表示未外部核验的LLM推断。"], ["说明4","当前结果为MODEL_VALIDATION和NON_ACTIONABLE。"],
  ...(data.order_rows.length===0?[["03表为空原因","本次无交易候选：LLM入选0只，人工选择0只。"]]:[]),
  ["Flash V4分布",data.state_summary?.flash_distribution || "未提供"],
  ["Flash decision分布",data.state_summary?.decision_distribution || "未提供"],
  ["非零仓位数",data.state_summary?.nonzero_position_count || 0],
  ["零仓位原因分布",data.state_summary?.zero_position_reasons || "无"],
  ["当前错误数",data.errors?.length || 0],
  ["当前警告数",data.warnings?.length || 0],
  ["历史已解决错误数",data.resolved_historical_errors?.length || 0],
  ["总input tokens",totalInput], ["总output tokens",totalOutput], ["总成本(USD)",totalCost], ["成功调用数",successCalls], ["失败调用数",failedCalls],
  ["Token硬上限",data.budget?.limits?.daily_limit || 5000000], ["Token预警阈值",data.budget?.limits?.warning_threshold || 4000000],
  ["Flash实际Token",data.budget?.usage?.flash || 0], ["Pro实际Token",data.budget?.usage?.pro || 0],
  ["Repair实际Token",data.budget?.usage?.repair || 0], ["Connectivity实际Token",data.budget?.usage?.connectivity || 0],
  ["总实际Token",data.budget?.total || (totalInput+totalOutput)], ["剩余Token",data.budget?.remaining || 5000000-(totalInput+totalOutput)],
  ["安全预留",data.budget?.limits?.final_reserve || 300000], ["预警是否触发",data.budget?.warning_triggered?"是":"否"],
  ["Pro Candidate成功数",data.row_counts?.trading_candidate_count || 0],
  ["Pro Candidate失败数",data.row_counts?.trading_candidate_count ? 0 : "未完成"],
  ["Portfolio Summary",data.pro_resume?.portfolio_result?.overall_summary || "未完成"],
  ["Pro实际Token",data.pro_resume?.token_ledger?.current_resume_new_api_tokens || 0],
  ["Pipeline实际Token",data.pro_resume?.token_ledger?.pipeline_total_actual_api_tokens || data.budget?.total || 0],
  ["剩余Token预算",data.pro_resume?.token_ledger?.remaining_budget_from_known_actual || data.budget?.remaining || 0],
  ["旧不可恢复usage数",data.pro_resume?.token_ledger?.pro_v1_unavailable_count || 0],
];
info.getRange(`A2:B${meta.length+1}`).values = meta.map((row)=>row.map(safe));
info.getRange(`A2:A${meta.length+1}`).format = {fill:C.light,font:{bold:true,color:C.navy}};
info.getRange(`B2:B${meta.length+1}`).format.wrapText = true;
info.getRange(`B15:B16`).format.numberFormat = "#,##0.00";
const errorStart = meta.length + 4;
info.getRange(`A${errorStart-1}:G${errorStart-1}`).merge();
info.getRange(`A${errorStart-1}`).values = [["异常摘要"]];
info.getRange(`A${errorStart-1}`).format = {fill:C.navy2,font:{bold:true,color:C.white}};
const eHeaders = ["股票代码","模块","状态","简洁错误原因","是否影响入选","是否影响挂单","是否影响仓位"];
const eRows = [...(data.errors || []), ...(data.warnings || [])].map((r)=>[r.stock_code,r.module,r.status,r.reason,r.affects_selection,r.affects_order,r.affects_position]);
const e = tableBlock(info,errorStart,eHeaders,eRows,"TraderDemoExceptionsTable");
info.freezePanes.freezeRows(1);
info.getRange(`A${errorStart+1}:A${e.endRow}`).format.numberFormat = "@";
contains(info.getRange(`C${errorStart+1}:G${e.endRow}`),"SCHEMA_ERROR",C.red,C.redText);
contains(info.getRange(`C${errorStart+1}:G${e.endRow}`),"BLOCKED",C.red,C.redText);
contains(info.getRange(`C${errorStart+1}:G${e.endRow}`),"PASS",C.green,C.greenText);
setWidth(info,1,22); setWidth(info,2,34); setWidth(info,3,18); setWidth(info,4,42); setWidth(info,5,14); setWidth(info,6,14); setWidth(info,7,14);

await fs.mkdir(path.dirname(outputPath), {recursive:true});
if (previewDir) {
  await fs.mkdir(previewDir,{recursive:true});
  const ranges = {"01_全A量化排名":"A1:T24","02_Top100_LLM评分":"A1:AG20","03_挂单与仓位":"A1:AP20","04_重点基本面":"A1:BD16","05_说明与异常":`A1:G${Math.min(e.endRow,48)}`};
  for (const name of names) {
    const image = await workbook.render({sheetName:name,range:ranges[name],scale:0.9,format:"png"});
    await fs.writeFile(path.join(previewDir,`${name}.png`),new Uint8Array(await image.arrayBuffer()));
  }
}
const keyInspect = await workbook.inspect({kind:"table",range:"03_挂单与仓位!A1:AP20",include:"values,formulas",tableMaxRows:20,tableMaxCols:42,maxChars:8000});
const formulaErrors = await workbook.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:300},summary:"final formula error scan",maxChars:4000});
await fs.writeFile(`${outputPath}.inspect.ndjson`,`${keyInspect.ndjson}\n${formulaErrors.ndjson}\n`,"utf8");
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
