<template>
  <section class="post-close-page">
    <div class="page-title">
      <div><h1>盘后操作建议</h1><p>快速规则先生成，iFinD 仅做影子对比，当前采用建议仍使用 Tushare 原方案。</p></div>
      <div class="actions">
        <el-select v-model="positionAccountScope" class="scope-select" aria-label="持仓账户范围">
          <el-option label="人工账户" value="HUMAN_REFERENCE" />
          <el-option label="AI 模拟账户" value="AI_SIMULATION" />
        </el-select>
        <el-checkbox v-model="includeAiSimulation" @change="refresh">同时评估 AI 模拟账户</el-checkbox>
        <el-button type="primary" :loading="loading" :disabled="!positionTruth.action_run_allowed" @click="runFast">2. 生成快速建议</el-button>
        <el-button :loading="loading" :disabled="!positionTruth.action_run_allowed" @click="runFinal">5. 生成最终建议</el-button>
        <el-button @click="refresh">使用已有结果</el-button>
        <el-button :disabled="!finalRun?.run_id" @click="runPro">6. 运行 Pro 复核</el-button>
        <el-button :disabled="!fastRun?.run_id || !finalRun?.run_id" @click="showCompare">7. 查看 Fast/Final 差异</el-button>
        <el-button @click="showRules">查看规则解释</el-button>
        <el-button :disabled="!status.run_id" @click="exportExcel">8. 导出 Excel</el-button>
        <el-button @click="choosePositionFile">1. 导入/更新持仓</el-button>
        <el-button type="warning" @click="confirmEmpty">1. 确认所选账户空仓</el-button>
        <input ref="fileInput" type="file" accept=".csv,.xlsx" hidden @change="previewFile" />
      </div>
    </div>

    <el-alert type="warning" show-icon :closable="false" title="本功能只提供人工决策参考，不会自动卖出、减仓或创建订单。" />
    <el-alert v-if="!positionTruth.action_run_allowed" type="error" show-icon :closable="false" title="尚未确认持仓，无法判断继续持有、减仓或退出。请导入持仓或确认当前为空仓。" />
    <el-alert v-if="fastRun?.run_id" type="info" show-icon :closable="false" title="盘后初步建议：基于已完成的收盘快照、分钟结构和现有持仓事实，尚未完成当日 Tushare 最终数据与完整模型复核。" />

    <div class="truth-grid">
      <el-card><span>持仓快照状态</span><strong>{{ positionTruth.status }}</strong></el-card>
      <el-card><span>快照时间</span><strong>{{ positionTruth.snapshot_time || "-" }}</strong></el-card>
      <el-card><span>人工 / AI 持仓</span><strong>{{ positionTruth.human_count }} / {{ positionTruth.ai_count }}</strong></el-card>
      <el-card><span>所需范围</span><strong>{{ requiredScopeLabel }}</strong></el-card>
      <el-card><span>人工账户</span><strong>{{ positionTruth.scope_status.HUMAN_REFERENCE || "MISSING" }}</strong></el-card>
      <el-card><span>AI 模拟账户</span><strong>{{ positionTruth.scope_status.AI_SIMULATION || "NOT_REQUIRED" }}</strong></el-card>
      <el-card><span>已确认空仓</span><strong>{{ positionTruth.confirmed_empty ? "是" : "否" }}</strong></el-card>
    </div>

    <div class="run-grid">
      <el-card><span>盘后初步</span><strong>{{ fastRun?.status || "NOT_RUN" }}</strong><small>Pool来源：{{ String(fastRun?.pool?.source_trade_date || "-") }}</small><small>Full / Partial：{{ String(fastRun?.tiered_coverage?.full_overlay_count ?? 0) }} / {{ String(fastRun?.tiered_coverage?.partial_only_count ?? 0) }}</small></el-card>
      <el-card><span>盘后最终</span><strong>{{ finalRun?.status || "BLOCKED_DATA_NOT_READY" }}</strong><small>Tushare与今日Pipeline完成后方可运行</small></el-card>
    </div>

    <div class="summary-grid">
      <el-card><span>状态</span><strong>{{ status.status || "NOT_RUN" }}</strong></el-card>
      <el-card><span>交易日 / 目标日</span><strong>{{ status.trade_date || store.tradeDate }} / {{ status.target_trade_date || "-" }}</strong></el-card>
      <el-card><span>持仓 / 未持仓</span><strong>{{ status.held_count ?? 0 }} / {{ status.non_held_count ?? 0 }}</strong></el-card>
      <el-card><span>评分 Profile</span><strong>{{ status.scoring_profile || "TUSHARE_BASELINE_V1" }}</strong></el-card>
      <el-card><span>iFinD 模式</span><strong>{{ status.ifind_mode || "SHADOW" }}</strong></el-card>
      <el-card><span>Fast 耗时</span><strong>{{ status.rule_duration_ms ?? 0 }} ms</strong></el-card>
      <el-card><span>Pro 状态</span><strong>{{ status.pro_review_status || "NOT_RUN" }}</strong></el-card>
      <el-card><span>人工复核</span><strong>{{ status.manual_review_count ?? 0 }}</strong></el-card>
      <el-card><span>继续持有</span><strong>{{ actionCount("CONTINUE_HOLD") }}</strong></el-card>
      <el-card><span>收紧止损</span><strong>{{ actionCount("HOLD_WITH_TIGHT_STOP") }}</strong></el-card>
      <el-card><span>减仓</span><strong>{{ actionCount("REDUCE_POSITION") }}</strong></el-card>
      <el-card><span>退出准备</span><strong>{{ actionCount("EXIT_NEXT_SESSION") + actionCount("EXIT_WHEN_TRADABLE") + actionCount("T_PLUS_ONE_LOCKED_EXIT_PLAN") }}</strong></el-card>
    </div>

    <el-card class="panel">
      <template #header><div class="panel-head"><span>操作建议</span><div class="actions"><el-button type="primary" size="small" :disabled="!monitorActionRows.length" @click="addActionsToMonitor">加入盯盘</el-button><el-segmented v-model="scope" :options="scopeOptions" @change="loadResults" /></div></div></template>
      <CenteredDataTable :rows="rows" :columns="columns" :loading="loading" :total="total" :current-page="page" :page-size="pageSize" selectable height="520" @pagination-change="changePage" @selection-change="handleActionSelection" />
    </el-card>

    <el-card class="panel">
      <template #header><div class="panel-head"><span>当前人工与模拟持仓快照</span><div class="actions"><el-button type="primary" size="small" :disabled="!monitorPositionRows.length" @click="addPositionsToMonitor">加入盯盘</el-button><el-button size="small" @click="loadPositions">刷新</el-button></div></div></template>
      <CenteredDataTable :rows="positions" :columns="positionColumns" :pagination-enabled="false" selectable height="260" @selection-change="handlePositionSelection" />
    </el-card>

    <el-card class="panel">
      <template #header><span>历史版本</span></template>
      <CenteredDataTable :rows="history" :columns="historyColumns" :pagination-enabled="false" height="260" @row-double-click="useHistory" />
    </el-card>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";

import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import { useMonitorPool } from "@/composables/useMonitorPool";
import { confirmEmptyPositions, confirmPositionImport, exportPostClose, getCurrentPositions, getFastFinalCompare, getPositionTruthStatus, getPostCloseHistory, getPostCloseResults, getPostCloseStatus, previewPositionImport, runPostCloseFast, runPostClosePro } from "@/api/postClose";
import type { PositionRow, PositionTruthStatus, PostCloseActionRow, PostCloseStatus } from "@/types/postClose";
import type { MonitorPoolCandidate } from "@/types/realtime";
import { useWorkbenchStore } from "@/stores/workbench";

const store = useWorkbenchStore();
const status = ref<PostCloseStatus>({ status: "NOT_RUN" });
const emptyTruth: PositionTruthStatus = { status: "MISSING", human_count: 0, ai_count: 0, confirmed_empty: false, missing_fields: [], action_run_allowed: false, scope_status: {}, required_scopes: ["HUMAN_REFERENCE"], maximum_snapshot_age_hours: 24, ai_simulation_required: false, scope_details: {} };
const positionTruth = ref<PositionTruthStatus>({ ...emptyTruth });
const rows = ref<PostCloseActionRow[]>([]);
const positions = ref<PositionRow[]>([]);
const history = ref<PostCloseStatus[]>([]);
const monitorActionRows = ref<Record<string, unknown>[]>([]);
const monitorPositionRows = ref<Record<string, unknown>[]>([]);
const { addToMonitor } = useMonitorPool();
const loading = ref(false);
const scope = ref("ALL");
const page = ref(1);
const pageSize = ref(50);
const total = ref(0);
const fileInput = ref<HTMLInputElement>();
const positionAccountScope = ref<"HUMAN_REFERENCE" | "AI_SIMULATION">("HUMAN_REFERENCE");
const includeAiSimulation = ref(false);
const fastRun = computed(() => history.value.find(item => item.run_mode === "POST_CLOSE_FAST"));
const finalRun = computed(() => history.value.find(item => item.run_mode === "POST_CLOSE_FINAL"));
const requiredScopeLabel = computed(() => (positionTruth.value.required_scopes || ["HUMAN_REFERENCE"]).map(scope => scope === "HUMAN_REFERENCE" ? "人工账户" : "AI 模拟账户").join("、"));
const scopeOptions = [{ label: "全部", value: "ALL" }, { label: "持仓", value: "HELD" }, { label: "未持仓", value: "NOT_HELD" }];
const actionLabel: Record<string, string> = { CONTINUE_HOLD: "继续持有", HOLD_WITH_TIGHT_STOP: "继续持有并收紧止损", REDUCE_POSITION: "建议减仓", EXIT_NEXT_SESSION: "次日退出准备", EXIT_WHEN_TRADABLE: "可交易时退出", T_PLUS_ONE_LOCKED_EXIT_PLAN: "今日 T+1 锁定，准备下一交易日退出", MANUAL_REVIEW: "人工复核", DATA_INSUFFICIENT: "数据不足", PREPARE_ENTRY: "准备次日计划", KEEP_WATCH: "继续观察", DO_NOT_CHASE: "不宜追高", REMOVE_FROM_POOL: "移出候选" };
const columns = [
  { key: "stock_code", label: "股票代码", minWidth: 105 }, { key: "stock_name", label: "股票名称", minWidth: 100 },
  { key: "position_status", label: "是否持仓", minWidth: 120, formatter: (v: unknown) => v === "SELECTED_NOT_HELD" ? "否" : v === "POSITION_DATA_MISSING" ? "持仓数据缺失" : "是" },
  { key: "account_scope", label: "持仓账户类型", minWidth: 125 }, { key: "selection_source", label: "选择来源", minWidth: 150 },
  { key: "quantity", label: "当前数量" }, { key: "available_quantity", label: "当前可卖" }, { key: "target_day_sellable_quantity", label: "次日可卖" },
  { key: "cost_price", label: "成本价" }, { key: "close_price", label: "收盘价" }, { key: "unrealized_return", label: "浮盈亏", formatter: percent }, { key: "holding_days", label: "持有天数" },
  { key: "base_score", label: "Base分" }, { key: "base_rank", label: "Base排名" }, { key: "enhanced_shadow_score", label: "iFinD增强分" }, { key: "enhanced_rank", label: "增强排名" }, { key: "rank_delta", label: "排名变化", formatter: rankDelta },
  { key: "action_health_score", label: "健康分" },
  { key: "data_quality_status", label: "数据质量", minWidth: 115 }, { key: "hard_gate_status", label: "Hard Gate", minWidth: 130 },
  { key: "baseline_rule_action", label: "原方案建议", minWidth: 150, formatter: action }, { key: "ifind_shadow_action", label: "iFinD影子建议", minWidth: 150, formatter: action },
  { key: "pro_review_action", label: "Pro复核", minWidth: 130, formatter: action }, { key: "current_adopted_action", label: "当前采用建议", minWidth: 170, formatter: action },
  { key: "current_position_percent", label: "当前仓位", formatter: percent }, { key: "suggested_target_position_percent", label: "目标仓位", formatter: percent },
  { key: "suggested_reduce_percent", label: "建议减仓比例", formatter: percent }, { key: "suggested_reduce_quantity", label: "建议减仓股数" },
  { key: "stop_loss_price", label: "止损价" }, { key: "take_profit_1", label: "止盈1" }, { key: "take_profit_2", label: "止盈2" },
  { key: "key_reasons_json", label: "主要依据", minWidth: 220, formatter: list }, { key: "key_risks_json", label: "主要风险", minWidth: 220, formatter: list },
  { key: "requires_manual_review", label: "人工复核", formatter: (v: unknown) => v ? "是" : "否" }, { key: "advice_version", label: "建议版本", minWidth: 150 }, { key: "created_at", label: "生成时间", minWidth: 170 },
];
const positionColumns = [{ key: "stock_code", label: "股票代码" }, { key: "stock_name", label: "股票名称" }, { key: "account_scope", label: "账户类型" }, { key: "quantity", label: "数量" }, { key: "available_quantity", label: "可卖数量" }, { key: "cost_price", label: "成本价" }, { key: "buy_date", label: "买入日期" }, { key: "source", label: "来源" }];
const historyColumns = [{ key: "trade_date", label: "交易日" }, { key: "target_trade_date", label: "目标交易日" }, { key: "run_mode", label: "模式" }, { key: "status", label: "状态" }, { key: "stock_count", label: "股票数" }, { key: "held_count", label: "持仓数" }, { key: "rule_duration_ms", label: "耗时(ms)" }, { key: "pro_review_status", label: "Pro状态" }];

async function refresh() { const [response, truth] = await Promise.all([getPostCloseStatus(undefined, store.tradeDate), getPositionTruthStatus(store.tradeDate, includeAiSimulation.value)]); status.value = response.data; positionTruth.value = { ...emptyTruth, ...truth.data, scope_status: truth.data.scope_status || {}, required_scopes: truth.data.required_scopes || ["HUMAN_REFERENCE"], scope_details: truth.data.scope_details || {} }; await Promise.all([loadResults(), loadPositions(), loadHistory()]); }
async function runFast() {
  try {
    await ElMessageBox.confirm("将生成只读盘后规则建议，不会自动卖出、减仓或创建订单。确认继续吗？", "生成盘后快速建议", { type: "warning", confirmButtonText: "确认生成", cancelButtonText: "取消" });
    loading.value = true;
    status.value = (await runPostCloseFast(store.tradeDate, "POST_CLOSE_FAST", includeAiSimulation.value)).data;
    if (status.value.status === "WAITING_FOR_POST_CLOSE_RUN") {
      rows.value = [];
      total.value = 0;
      await Promise.all([loadPositions(), loadHistory()]);
    } else {
      await refresh();
    }
    ElMessage.success(status.value.status === "WAITING_FOR_POST_CLOSE_RUN" ? "尚未收盘，已保持等待状态" : "快速建议已生成");
  } catch (error) { if (error !== "cancel") ElMessage.error(error instanceof Error ? error.message : "生成失败"); }
  finally { loading.value = false; }
}
async function runFinal() { try { await ElMessageBox.confirm("最终建议仅在当日 Tushare 核心数据和正式 Pipeline 完成后生成。确认检查并运行吗？", "生成盘后最终建议", { type: "warning" }); loading.value=true; status.value=(await runPostCloseFast(store.tradeDate,"POST_CLOSE_FINAL",includeAiSimulation.value)).data; await refresh(); ElMessage.info(status.value.status); } catch(error) { if(error!=="cancel") ElMessage.error(error instanceof Error?error.message:"生成失败"); } finally { loading.value=false; } }
async function runPro() { if (!finalRun.value?.run_id) return; await runPostClosePro(finalRun.value.run_id); ElMessage.info("Pro 复核已排队，规则结果保持可用"); }
async function loadResults() { if (!status.value.run_id) { rows.value=[]; total.value=0; return; } const held = scope.value === "ALL" ? undefined : scope.value === "HELD"; const response=await getPostCloseResults(status.value.run_id,page.value,pageSize.value,held); rows.value=response.data.items; total.value=response.data.total; }
async function loadPositions() { positions.value=(await getCurrentPositions(store.tradeDate,includeAiSimulation.value)).data.items; }
async function loadHistory() { history.value=(await getPostCloseHistory(store.tradeDate)).data.items; }
async function showCompare() { if (!fastRun.value?.run_id || !finalRun.value?.run_id) return; await ElMessageBox.alert(JSON.stringify((await getFastFinalCompare(store.tradeDate)).data,null,2),"Fast 与 Final 差异",{confirmButtonText:"关闭"}); }
async function showRules() { await ElMessageBox.alert("Fast 建议由配置化规则计算。持仓与未持仓使用不同动作集合；Hard Gate 优先；iFinD 仅展示影子建议；Pro 只能确认、更保守或要求人工复核。系统不会创建订单。", "盘后建议规则", { confirmButtonText: "关闭" }); }
async function exportExcel() { if (!status.value.run_id) return; const result=(await exportPostClose(status.value.run_id)).data; ElMessage.success(`Excel 已生成：${String(result.output_path || "")}`); }
function choosePositionFile() { fileInput.value?.click(); }
async function previewFile(event: Event) { const file=(event.target as HTMLInputElement).files?.[0]; if(!file)return; const base64=await fileBase64(file); const preview=(await previewPositionImport(file.name,base64,positionAccountScope.value)).data as {preview_id?:string;status?:string;row_count?:number;errors?:unknown[]}; if(preview.status!=="VALID"){await ElMessageBox.alert(JSON.stringify(preview.errors||[],null,2),"持仓文件校验失败");return;} await ElMessageBox.confirm(`已校验 ${preview.row_count} 行${positionAccountScope.value === "HUMAN_REFERENCE" ? "人工账户" : "AI 模拟账户"}持仓，确认写入新的不可变持仓快照吗？`,"确认持仓快照",{type:"warning"}); await confirmPositionImport(String(preview.preview_id)); await refresh(); ElMessage.success("持仓快照已更新"); }
async function confirmEmpty() { const label=positionAccountScope.value === "HUMAN_REFERENCE" ? "人工账户" : "AI 模拟账户"; await ElMessageBox.confirm(`该操作只会将${label}明确确认为当前空仓，并保存不可变快照，不会改动另一账户。确认继续吗？`, `确认${label}空仓`, { type: "warning", confirmButtonText: "确认空仓", cancelButtonText: "取消" }); await confirmEmptyPositions(store.tradeDate,[positionAccountScope.value]); await refresh(); ElMessage.success(`${label}空仓事实已确认`); }
function handleActionSelection(value: Record<string, unknown>[]) { monitorActionRows.value = value; }
function handlePositionSelection(value: Record<string, unknown>[]) { monitorPositionRows.value = value; }
async function addActionsToMonitor() {
  const candidates: MonitorPoolCandidate[] = monitorActionRows.value.map((row) => ({
    stock_code: String(row.stock_code || ""), stock_name: String(row.stock_name || ""), sources: ["POST_CLOSE_ACTION"],
    monitor_profile: row.position_status === "SELECTED_NOT_HELD" ? "CANDIDATE_MONITOR" : "POSITION_RISK_MONITOR",
    priority: row.position_status === "SELECTED_NOT_HELD" ? "NORMAL" : "HIGH",
    stop_loss: numberOrNull(row.stop_loss_price), take_profit_1: numberOrNull(row.take_profit_1), take_profit_2: numberOrNull(row.take_profit_2)
  }));
  await addToMonitor(store.tradeDate, candidates, "POST_CLOSE_ACTION", status.value.run_id);
}
async function addPositionsToMonitor() {
  const candidates: MonitorPoolCandidate[] = monitorPositionRows.value.map((row) => ({
    stock_code: String(row.stock_code || ""), stock_name: String(row.stock_name || ""),
    sources: [row.account_scope === "AI_SIMULATION" ? "AI_POSITION" : "HUMAN_POSITION"],
    monitor_profile: "POSITION_RISK_MONITOR", priority: "HIGH"
  }));
  await addToMonitor(store.tradeDate, candidates, "MULTIPLE");
}
function numberOrNull(value: unknown): number | null { const result = Number(value); return Number.isFinite(result) ? result : null; }
function useHistory(row: Record<string, unknown>) { const selected=row as unknown as PostCloseStatus; if(!selected.run_id)return; status.value=selected; page.value=1; void loadResults(); }
function changePage(value:{page:number;pageSize:number}) { page.value=value.page; pageSize.value=value.pageSize; void loadResults(); }
function action(value: unknown) { return actionLabel[String(value || "")] || String(value || "-"); }
function percent(value: unknown) { return value == null ? "-" : `${(Number(value)*100).toFixed(2)}%`; }
function rankDelta(_value: unknown, row: Record<string, unknown>) { const base=Number(row.base_rank); const enhanced=Number(row.enhanced_rank); return Number.isFinite(base)&&Number.isFinite(enhanced) ? String(base-enhanced) : "-"; }
function actionCount(value: string) { return status.value.action_distribution?.[value] ?? 0; }
function list(value: unknown) { return Array.isArray(value) ? value.join("\n") : String(value || "-"); }
function fileBase64(file: File): Promise<string> { return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]||"");reader.onerror=()=>reject(reader.error);reader.readAsDataURL(file);}); }
onMounted(() => void refresh());
</script>

<style scoped>
.post-close-page { display: grid; gap: 14px; }
.page-title, .panel-head, .actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.page-title h1 { margin: 0; font-size: 22px; }
.page-title p { margin: 6px 0 0; color: #667085; }
.actions { justify-content: flex-end; flex-wrap: wrap; }
.scope-select { width: 132px; }
.summary-grid, .truth-grid, .run-grid { display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 12px; }
.run-grid { grid-template-columns: repeat(2, minmax(240px, 1fr)); }
.summary-grid span, .summary-grid strong, .truth-grid span, .truth-grid strong, .run-grid span, .run-grid strong, .run-grid small { display: block; text-align: center; }
.summary-grid span { color: #667085; font-size: 12px; }
.summary-grid strong, .truth-grid strong { margin-top: 8px; font-size: 15px; overflow-wrap: anywhere; }
.run-grid strong { margin: 8px 0; }
.run-grid small { color: #667085; line-height: 1.6; }
@media (max-width: 1000px) { .summary-grid { grid-template-columns: repeat(2, minmax(150px, 1fr)); } .page-title { align-items: flex-start; flex-direction: column; } .actions { justify-content: flex-start; } }
</style>
