<template>
  <section class="midday-page">
    <header class="page-head">
      <div>
        <h1>午间推荐</h1>
        <p>上一交易日量化基线与上午 iFinD 影子增强，服务于下午盘观察和人工决策。</p>
      </div>
      <div class="actions">
        <el-button type="primary" :loading="loading" @click="start">生成午间推荐</el-button>
        <el-button :loading="loading" @click="refresh">刷新</el-button>
        <el-button :disabled="!status.run_id" @click="recheck">下午复核</el-button>
        <el-button type="primary" :disabled="!monitorRows.length" @click="addMiddayToMonitor">加入下午盯盘</el-button>
        <el-button :disabled="!status.run_id" @click="exportExcel">导出 Excel</el-button>
      </div>
    </header>

    <el-alert v-if="status.status === 'FAILED'" type="error" show-icon :closable="false" title="V2.2 午盘运行失败，请查看审计状态。" />
    <el-alert v-else-if="Number(layerCounts.AFTERNOON_WATCH || 0) > 0" type="success" show-icon :closable="false" title="当前没有立即许可不等于没有候选，下午观察池等待真实复核。" />

    <div class="summary-grid">
      <div><span>状态</span><strong>{{ status.status || "NOT_RUN" }}</strong></div>
      <div><span>阶段</span><strong>{{ status.current_stage || "-" }}</strong></div>
      <div><span>基线交易日</span><strong>{{ status.baseline_trade_date || "-" }}</strong></div>
      <div><span>候选池</span><strong>{{ status.base_pool_count ?? 0 }}</strong></div>
      <div><span>快照 / 分钟线</span><strong>{{ status.snapshot_count ?? 0 }} / {{ status.minute_count ?? 0 }}</strong></div>
      <div><span>BUY_READY / 下午观察</span><strong>{{ layerCounts.BUY_READY || 0 }} / {{ layerCounts.AFTERNOON_WATCH || 0 }}</strong></div>
      <div><span>状态门禁 / 集中复核</span><strong>{{ layerCounts.REGIME_BLOCKED_HIGH_SCORE || 0 }} / {{ layerCounts.CONCENTRATION_REVIEW || 0 }}</strong></div>
      <div><span>午盘市场状态</span><strong>{{ status.previous_regime || "-" }} → {{ status.midday_regime || "-" }}</strong></div>
      <div><span>下午复核</span><strong>{{ status.afternoon_recheck_status || "NOT_RUN" }}</strong></div>
    </div>

    <div class="scope-row">
      <el-segmented v-model="scope" :options="scopeOptions" />
      <span>观察价格和升级条件均由规则计算，结果不会创建订单。</span>
    </div>
    <CenteredDataTable
      :rows="visibleRows"
      :columns="columns"
      :loading="loading"
      :total="total"
      :current-page="page"
      :page-size="pageSize"
      selectable
      height="calc(100vh - 350px)"
      @pagination-change="changePage"
      @selection-change="handleMonitorSelection"
    />
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";

import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import { useMonitorPool } from "@/composables/useMonitorPool";
import { getMiddayV22Results, getMiddayV22Status, recheckMiddayV22, runMiddayV22 } from "@/api/midday";
import { useWorkbenchStore } from "@/stores/workbench";
import type { MiddayResult, MiddayStatus } from "@/types/midday";
import type { MonitorPoolCandidate } from "@/types/realtime";

const store = useWorkbenchStore();
const loading = ref(false);
const status = ref<MiddayStatus>({ status: "NOT_RUN" });
const rows = ref<MiddayResult[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(50);
const scope = ref("ACTIONABLE");
const monitorRows = ref<Record<string, unknown>[]>([]);
const { addToMonitor } = useMonitorPool();
const scopeOptions = [{ label: "可买与观察", value: "ACTIONABLE" }, { label: "状态门禁", value: "REGIME" }, { label: "集中度复核", value: "CONCENTRATION" }, { label: "全部", value: "ALL" }];
const visibleRows = computed(() => scope.value === "ACTIONABLE" ? rows.value.filter(row => ["BUY_READY", "AFTERNOON_WATCH"].includes(String(row.result_layer))) : scope.value === "REGIME" ? rows.value.filter(row => row.result_layer === "REGIME_BLOCKED_HIGH_SCORE") : scope.value === "CONCENTRATION" ? rows.value.filter(row => row.result_layer === "CONCENTRATION_REVIEW") : rows.value);
const layerCounts = computed<Record<string, number>>(() => (status.value.counts?.result_layers as Record<string, number> | undefined) || {});
const actionLabels: Record<string, string> = { AFTERNOON_PREPARE_ENTRY: "午后准备介入", WAIT_PULLBACK: "等待回落", KEEP_WATCH: "继续观察", REMOVE_FROM_POOL: "移出候选池", DO_NOT_CHASE: "不追高", MANUAL_REVIEW: "人工复核", DATA_INSUFFICIENT: "数据不足", CONTINUE_HOLD: "继续持有", HOLD_WITH_TIGHT_STOP: "持有并收紧止损", REDUCE_IF_WEAKENS: "走弱时减仓", EXIT_IF_TRIGGERED: "触发条件时退出", T_PLUS_ONE_LOCKED: "T+1 锁定" };
const columns = [
  { key: "result_layer", label: "结果层级", minWidth: 190 },
  { key: "stock_code", label: "股票代码", minWidth: 110 },
  { key: "stock_name", label: "股票名称" },
  { key: "quant_rank", label: "基线排名" },
  { key: "quant_score", label: "基线分", formatter: score },
  { key: "strategy_id", label: "基础策略", minWidth: 150 },
  { key: "live_strategy_status", label: "午盘策略状态", minWidth: 150 },
  { key: "admission_score_v2_1", label: "准入分", formatter: score },
  { key: "admission_status_v2", label: "准入状态" },
  { key: "trigger_status", label: "分钟触发", minWidth: 150 },
  { key: "trigger_reasons", label: "未满足条件", minWidth: 220, formatter: list },
  { key: "current_price", label: "当前价", formatter: (_: unknown, row: Record<string, unknown>) => nested(row, "current_price") },
  { key: "maximum_acceptable_price", label: "最高接受价", formatter: (_: unknown, row: Record<string, unknown>) => nested(row, "maximum_acceptable_price") },
  { key: "invalidation_price", label: "失效参考价", formatter: (_: unknown, row: Record<string, unknown>) => nested(row, "invalidation_price") },
  { key: "stop_loss_reference", label: "止损参考", formatter: (_: unknown, row: Record<string, unknown>) => nested(row, "stop_loss_reference") },
];

async function refresh() {
  loading.value = true;
  try {
    status.value = (await getMiddayV22Status(store.tradeDate)).data;
    if (status.value.run_id) {
      const response = await getMiddayV22Results(status.value.run_id, page.value, pageSize.value);
      rows.value = response.data.items;
      total.value = response.data.total;
    } else {
      rows.value = [];
      total.value = 0;
    }
  } finally { loading.value = false; }
}
async function start() {
  await ElMessageBox.confirm("系统将运行午间实时行情与模型复核，只生成建议，不创建订单。", "生成午间推荐", { type: "warning" });
  await runMiddayV22(store.tradeDate);
  ElMessage.success("任务已排队，请稍后刷新查看进度");
  await refresh();
}
async function recheck() { if (!status.value.run_id) return; const selected = monitorRows.value.map(row => String(row.stock_code || "")).filter(Boolean); const result = await recheckMiddayV22(store.tradeDate, selected); ElMessage.info(String(result.data.status || "已排队")); await refresh(); }
async function exportExcel() { if (!status.value.excel_path) return; ElMessage.success(`Excel 已生成：${status.value.excel_path}`); }
function handleMonitorSelection(value: Record<string, unknown>[]) { monitorRows.value = value; }
async function addMiddayToMonitor() {
  const candidates: MonitorPoolCandidate[] = monitorRows.value.map((row) => ({
    stock_code: String(row.stock_code || ""), stock_name: String(row.stock_name || ""),
    sources: ["MIDDAY_V22"], monitor_profile: "CANDIDATE_MONITOR",
    priority: row.result_layer === "BUY_READY" ? "HIGH" : "NORMAL",
    recommended_price: numberOrNull((row.afternoon_recheck as Record<string, unknown> | undefined)?.current_price), max_acceptable_price: numberOrNull((row.afternoon_recheck as Record<string, unknown> | undefined)?.maximum_acceptable_price),
    stop_loss: numberOrNull((row.afternoon_recheck as Record<string, unknown> | undefined)?.stop_loss_reference), take_profit_1: null, take_profit_2: null
  }));
  await addToMonitor(store.tradeDate, candidates, "MIDDAY_RECOMMENDATION", status.value.run_id);
}
function numberOrNull(value: unknown): number | null { const result = Number(value); return Number.isFinite(result) ? result : null; }
function nested(row: Record<string, unknown>, key: string) { return String(((row.afternoon_recheck as Record<string, unknown> | undefined) || {})[key] ?? "-"); }
function changePage(value: { page: number; pageSize: number }) { page.value = value.page; pageSize.value = value.pageSize; void refresh(); }
function shortTime(value?: string) { return value ? value.slice(11, 16) : "-"; }
function score(value: unknown) { return value == null ? "-" : Number(value).toFixed(2); }
function signed(value: unknown) { return value == null ? "-" : `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(2)}`; }
function percent(value: unknown) { return value == null ? "-" : `${(Number(value) * 100).toFixed(2)}%`; }
function list(value: unknown) { return Array.isArray(value) ? value.join("、") : String(value || "-"); }
function action(value: unknown) { return actionLabels[String(value || "")] || String(value || "-"); }
onMounted(() => void refresh());
</script>

<style scoped>
.midday-page { display: grid; gap: 16px; }
.page-head, .actions, .scope-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.page-head h1 { margin: 0; font-size: 22px; }
.page-head p, .scope-row span { margin: 6px 0 0; color: #667085; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); border: 1px solid #dfe5ec; background: #fff; }
.summary-grid div { min-height: 78px; padding: 14px; border-right: 1px solid #e8edf2; border-bottom: 1px solid #e8edf2; text-align: center; }
.summary-grid span, .summary-grid strong { display: block; }
.summary-grid span { color: #667085; font-size: 12px; }
.summary-grid strong { margin-top: 8px; overflow-wrap: anywhere; }
@media (max-width: 1000px) { .page-head { align-items: flex-start; flex-direction: column; } .summary-grid { grid-template-columns: repeat(2, minmax(140px, 1fr)); } }
</style>
