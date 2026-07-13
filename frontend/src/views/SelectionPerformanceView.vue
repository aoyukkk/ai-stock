<template>
  <section>
    <div class="page-title">
      <div><h1>选股收益统计</h1><p>本统计反映选股后的市场价格表现，不代表实际成交收益或交易建议。</p></div>
      <StatusTag :status="String(cacheStatus.status ?? summary.status ?? 'NOT_RUN')" />
    </div>

    <el-card shadow="never" class="controls">
      <el-form label-position="top" class="control-grid">
        <el-form-item label="统计截止日期"><el-date-picker v-model="form.evaluation_end_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item label="日期模式"><el-segmented v-model="form.lookback_unit" :options="[{ label: '交易日数量', value: 'TRADING_DAYS' }, { label: '自定义范围', value: 'CUSTOM' }]" /></el-form-item>
        <el-form-item label="向前交易日数"><el-input-number v-model="form.lookback_value" :min="1" :max="120" :disabled="form.lookback_unit === 'CUSTOM'" /></el-form-item>
        <el-form-item label="自定义日期"><el-date-picker v-model="customRange" type="daterange" value-format="YYYY-MM-DD" :disabled="form.lookback_unit !== 'CUSTOM'" /></el-form-item>
        <el-form-item label="收益口径"><el-select v-model="form.return_basis"><el-option label="次日开盘起算" value="NEXT_OPEN" /><el-option label="信号收盘评价" value="SIGNAL_CLOSE" /></el-select></el-form-item>
        <el-form-item label="选股范围"><el-select v-model="form.selection_scope"><el-option v-for="item in scopes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <el-form-item label="加权方式"><el-select v-model="form.weighting_mode"><el-option label="等权" value="EQUAL_WEIGHT" /><el-option label="建议仓位加权" value="SUGGESTED_POSITION_WEIGHT" /></el-select></el-form-item>
        <el-form-item label="样本范围"><div><el-checkbox v-model="form.include_zero_position_stocks">包含零仓位</el-checkbox><el-checkbox v-model="form.include_risk_blocked_stocks">包含风险阻断</el-checkbox></div></el-form-item>
      </el-form>
      <div class="shortcuts"><span>快捷范围</span><el-button v-for="days in [5, 10, 20, 60]" :key="days" size="small" @click="quick(days)">近{{ days }}日</el-button></div>
      <div class="actions">
        <el-button type="primary" :loading="loading" @click="run(false)">更新统计</el-button>
        <el-button :disabled="!summary.performance_run_id" @click="load">使用已有结果</el-button>
        <el-button :disabled="!summary.performance_run_id" @click="incremental">增量更新</el-button>
        <el-button :disabled="!summary.performance_run_id" @click="run(true)">强制重新计算</el-button>
        <el-button :disabled="!summary.performance_run_id" @click="exportExcel">导出Excel</el-button>
        <el-button @click="methodologyVisible = true">查看计算说明</el-button>
      </div>
      <el-alert v-if="form.return_basis === 'SIGNAL_CLOSE'" title="信号评价口径，不代表可以在选股日收盘价成交。" type="warning" :closable="false" show-icon />
    </el-card>

    <el-alert v-if="cacheMessage" :title="cacheMessage" :type="cacheAlertType" :closable="false" show-icon class="cache-alert" />

    <div class="summary-grid">
      <div v-for="card in cards" :key="card.label" class="metric"><span>{{ card.label }}</span><strong>{{ card.value }}</strong></div>
    </div>

    <PerformanceCharts :daily="dailyRows" :cohorts="cohortRows" />

    <el-tabs v-model="activeTab" class="tables">
      <el-tab-pane label="按选股日汇总" name="cohorts">
        <CenteredDataTable :rows="cohortRows" :columns="cohortColumns" :loading="loading" :total="cohortTotal" :current-page="cohortPage" :page-size="cohortPageSize" @pagination-change="changeCohortPage" />
      </el-tab-pane>
      <el-tab-pane label="组合每日涨跌" name="daily">
        <CenteredDataTable :rows="dailyRows" :columns="dailyColumns" :loading="loading" :total="dailyTotal" :current-page="dailyPage" :page-size="dailyPageSize" @pagination-change="changeDailyPage" />
      </el-tab-pane>
      <el-tab-pane label="个股收益明细" name="stocks">
        <div class="table-toolbar"><el-input v-model="keyword" clearable placeholder="搜索股票代码或名称" @change="loadStocks" /></div>
        <CenteredDataTable :rows="stockRows" :columns="stockColumns" :loading="loading" :total="stockTotal" :current-page="stockPage" :page-size="stockPageSize" @pagination-change="changeStockPage" @row-double-click="openStock" />
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="drawerVisible" title="个股收益详情" size="62%">
      <CenteredDataTable :rows="drawerRows" :columns="stockColumns" height="calc(100vh - 160px)" />
    </el-drawer>
    <el-dialog v-model="methodologyVisible" title="计算口径" width="620px">
      <CenteredDataTable :rows="methodologyRows" :columns="[{ key: 'item', label: '项目' }, { key: 'value', label: '说明', minWidth: 360 }]" height="420" />
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import { performanceApi } from "@/api/performance";
import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import StatusTag from "@/components/common/StatusTag.vue";
import PerformanceCharts from "@/components/performance/PerformanceCharts.vue";
import type { PerformanceRequest, PerformanceSummary } from "@/types/performance";
import type { TableColumn } from "@/types/workbench";

const form = reactive<PerformanceRequest>({ evaluation_end_date: "2026-07-10", lookback_value: 5, lookback_unit: "TRADING_DAYS", start_selection_date: null, end_selection_date: null, return_basis: "NEXT_OPEN", selection_scope: "FINAL_CANDIDATES", weighting_mode: "EQUAL_WEIGHT", include_zero_position_stocks: true, include_risk_blocked_stocks: true, force_recalculate: false });
const customRange = ref<[string, string] | null>(null);
const scopes = [
  { label: "全部最终候选", value: "FINAL_CANDIDATES" }, { label: "仅LLM", value: "LLM_ONLY" },
  { label: "仅人工", value: "MANUAL_ONLY" }, { label: "仅BOTH", value: "BOTH_ONLY" },
  { label: "非零仓位", value: "NON_ZERO_POSITION" }, { label: "含零仓位全部候选", value: "ALL_CANDIDATES_INCLUDING_ZERO_POSITION" }
];
const summary = reactive<PerformanceSummary>({ status: "NOT_RUN", cohort_count: 0, stock_count: 0 });
const cacheStatus = reactive<Record<string, unknown>>({});
const cohortRows = ref<Record<string, unknown>[]>([]); const dailyRows = ref<Record<string, unknown>[]>([]); const stockRows = ref<Record<string, unknown>[]>([]);
const cohortTotal = ref(0); const dailyTotal = ref(0); const stockTotal = ref(0);
const cohortPage = ref(1); const dailyPage = ref(1); const stockPage = ref(1);
const cohortPageSize = ref(50); const dailyPageSize = ref(50); const stockPageSize = ref(50);
const activeTab = ref("cohorts"); const keyword = ref(""); const loading = ref(false);
const drawerVisible = ref(false); const selectedStock = ref(""); const methodologyVisible = ref(false); const methodologyRows = ref<Record<string, unknown>[]>([]);

const pct = (value: unknown) => value === null || value === undefined ? "-" : `${(Number(value) * 100).toFixed(2)}%`;
const percentageKeys = new Set(["day_1_return", "day_2_return", "day_3_return", "day_4_return", "day_5_return", "latest_daily_return", "daily_return", "cumulative_return", "win_rate", "drawdown_to_date", "max_drawdown_to_date", "coverage_ratio", "suggested_position_percent"]);
const parseColumns = (values: string[]): TableColumn[] => values.map((value) => { const [key, label] = value.split("|"); return { key, label, minWidth: ["pipeline_run_id", "data_status"].includes(key) ? 210 : 120, formatter: percentageKeys.has(key) ? pct : undefined }; });
const cohortColumns = parseColumns(["selection_trade_date|选股日期", "pipeline_run_id|Pipeline Run", "candidate_count|候选数量", "llm_count|LLM数量", "manual_count|人工数量", "both_count|BOTH数量", "baseline_trade_date|起算日期", "holding_days|持有交易日", "day_1_return|第1日", "day_2_return|第2日", "day_3_return|第3日", "day_4_return|第4日", "day_5_return|第5日", "latest_daily_return|最新单日", "cumulative_return|总涨跌幅", "positive_stock_count|正收益股票", "negative_stock_count|负收益股票", "win_rate|胜率", "max_drawdown|最大回撤", "coverage_ratio|覆盖率", "cache_status|缓存状态", "run_status|运行状态"]);
const dailyColumns = parseColumns(["selection_trade_date|选股日期", "pipeline_run_id|Pipeline Run", "evaluation_trade_date|评价日期", "holding_day|持有日", "weighting_mode|加权方式", "total_member_count|组合股票数", "valid_member_count|有效数据", "suspended_count|停牌数", "missing_count|缺失数", "daily_return|当日涨跌幅", "cumulative_return|累计涨跌幅", "win_rate|当日胜率", "best_stock_code|最佳股票", "worst_stock_code|最差股票", "drawdown_to_date|当前回撤", "max_drawdown_to_date|最大回撤", "coverage_ratio|覆盖率", "status|状态"]);
const stockColumns = parseColumns(["selection_trade_date|选股日期", "pipeline_run_id|Pipeline Run", "stock_code|股票代码", "stock_name|股票名称", "selection_source|选择来源", "quant_rank|Quant排名", "flash_rank|Flash排名", "pro_rank|Pro排名", "suggested_position_percent|建议仓位", "return_basis|起算方式", "baseline_trade_date|基准日期", "baseline_price|基准价格", "evaluation_trade_date|评价日期", "holding_day|持有日", "open_price|开盘价", "close_price|收盘价", "daily_return|当日涨跌幅", "cumulative_return|总涨跌幅", "drawdown_to_date|当前回撤", "max_drawdown_to_date|最大回撤", "return_source|数据来源", "data_status|数据状态"]);

const cards = computed(() => [
  { label: "选股日期数", value: summary.cohort_count ?? 0 }, { label: "去重股票数", value: summary.stock_count ?? 0 },
  { label: "平均单日涨跌幅", value: pct(summary.average_daily_return) }, { label: "平均累计涨跌幅", value: pct(summary.average_cumulative_return) },
  { label: "正收益股票比例", value: pct(summary.positive_stock_ratio) }, { label: "正收益组合比例", value: pct(summary.positive_portfolio_ratio) },
  { label: "最佳 / 最差选股日", value: `${summary.best_selection_date ?? "-"} / ${summary.worst_selection_date ?? "-"}` },
  { label: "最大组合回撤", value: pct(summary.max_drawdown) }, { label: "数据覆盖率", value: pct(summary.coverage_ratio) },
  { label: "缓存状态", value: summary.cache_status ?? "-" }, { label: "最新评价日期", value: summary.latest_evaluation_date ?? "-" }
]);
const cacheMessage = computed(() => {
  const status = String(cacheStatus.status ?? "");
  if (status === "STALE") return `现有收益统计仅计算至 ${cacheStatus.cached_end_date}，本地行情已更新至 ${cacheStatus.latest_market_date}。需要增量更新后才能显示最新累计涨跌幅。`;
  if (status === "PARTIAL_SUCCESS") return "当前结果存在行情缺失或未完成评价记录，不能视为完整统计。";
  if (status === "INVALIDATED") return "输入数据、算法版本或缓存校验已变化，原结果已失效，必须重新计算。";
  return "";
});
const cacheAlertType = computed(() => String(cacheStatus.status) === "INVALIDATED" ? "error" : "warning");
const drawerRows = computed(() => stockRows.value.filter((row) => row.stock_code === selectedStock.value));

function quick(days: number) { form.lookback_unit = "TRADING_DAYS"; form.lookback_value = days; customRange.value = null; }
function request(force: boolean): PerformanceRequest { const range = customRange.value; return { ...form, force_recalculate: force, start_selection_date: form.lookback_unit === "CUSTOM" ? range?.[0] ?? null : null, end_selection_date: form.lookback_unit === "CUSTOM" ? range?.[1] ?? null : null }; }
async function run(force: boolean) { loading.value = true; try { const response = await performanceApi.run(request(force)); ElMessage.success(response.data.duplicate_status === "SUCCESS_CACHE_HIT" ? "已使用相同口径的现有结果" : "收益统计任务已完成"); await load(); } catch { ElMessage.error("收益统计启动失败"); } finally { loading.value = false; } }
async function incremental() { if (!summary.performance_run_id) return; loading.value = true; try { await performanceApi.incremental(summary.performance_run_id, form.evaluation_end_date); await load(); ElMessage.success("增量更新已完成"); } finally { loading.value = false; } }
async function exportExcel() { if (!summary.performance_run_id) return; const response = await performanceApi.exportExcel(summary.performance_run_id); ElMessage.success(`已导出：${response.data.output_path ?? "收益统计Excel"}`); }
let requestSequence = 0;
async function load() { const sequence = ++requestSequence; loading.value = true; try { const [s, c, d, stocks, cache, methodology] = await Promise.all([performanceApi.summary(), performanceApi.cohorts(undefined, cohortPage.value, cohortPageSize.value), performanceApi.daily(undefined, dailyPage.value, dailyPageSize.value), performanceApi.stocks(undefined, stockPage.value, stockPageSize.value, keyword.value), performanceApi.cacheStatus(), performanceApi.methodology()]); if (sequence !== requestSequence) return; Object.assign(summary, s.data); Object.assign(cacheStatus, cache.data); cohortRows.value = c.data.items; cohortTotal.value = c.data.total; dailyRows.value = d.data.items; dailyTotal.value = d.data.total; stockRows.value = stocks.data.items; stockTotal.value = stocks.data.total; methodologyRows.value = Object.entries(methodology.data).map(([item, value]) => ({ item, value })); } finally { if (sequence === requestSequence) loading.value = false; } }
async function loadStocks() { stockPage.value = 1; await load(); }
function changeCohortPage(payload: { page: number; pageSize: number }) { cohortPage.value = payload.page; cohortPageSize.value = payload.pageSize; void load(); } function changeDailyPage(payload: { page: number; pageSize: number }) { dailyPage.value = payload.page; dailyPageSize.value = payload.pageSize; void load(); } function changeStockPage(payload: { page: number; pageSize: number }) { stockPage.value = payload.page; stockPageSize.value = payload.pageSize; void load(); }
function openStock(row: Record<string, unknown>) { selectedStock.value = String(row.stock_code); drawerVisible.value = true; }
onMounted(() => void load());
</script>

<style scoped>
.page-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.page-title h1 { margin: 0; font-size: 22px; }.page-title p { margin: 5px 0 0; color: #667085; }
.controls { margin-bottom: 12px; }.control-grid { display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 0 12px; }
.shortcuts, .actions { display: flex; align-items: center; justify-content: center; gap: 8px; margin: 8px 0; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); border: 1px solid #dfe5ec; background: #fff; }
.metric { min-height: 76px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-right: 1px solid #e7ebf0; border-bottom: 1px solid #e7ebf0; }
.metric span { color: #667085; font-size: 12px; }.metric strong { margin-top: 7px; font-size: 20px; }
.cache-alert { margin: 10px 0; }.tables { padding: 12px; background: #fff; }.table-toolbar { width: 320px; margin-bottom: 10px; }
</style>
