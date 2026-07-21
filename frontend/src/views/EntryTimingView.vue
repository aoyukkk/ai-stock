<template>
  <section class="entry-page">
    <header class="page-head">
      <div>
        <h1>短线买入准入分析</h1>
        <p>从候选通过、市场部署、行业集中到盘中触发的完整影子链路。</p>
      </div>
      <div class="actions">
        <el-segmented v-model="version" :options="versionOptions" @change="resetAndRefresh" />
        <el-button :loading="loading" @click="refresh">读取结果</el-button>
        <el-button type="primary" :loading="running" @click="runShadow">运行影子分析</el-button>
      </div>
    </header>

    <el-alert type="info" :closable="false" show-icon title="只读取本地缓存；不调用 LLM、外部行情接口，不创建真实或虚拟订单。" />

    <div v-if="version === 'V2.2'" class="summary-grid">
      <div class="metric"><span>Market Regime V2</span><strong>{{ v22Summary?.regime_state ?? "暂无" }}</strong></div>
      <div class="metric"><span>准入通过</span><strong>{{ v22Summary?.candidate_before ?? 0 }}</strong></div>
      <div class="metric"><span>状态部署后</span><strong>{{ v22Summary?.after_regime ?? 0 }}</strong></div>
      <div class="metric review"><span>行业集中后</span><strong>{{ v22Summary?.after_concentration ?? 0 }}</strong></div>
      <div class="metric pass"><span>盘中已触发</span><strong>{{ v22Summary?.triggered_count ?? 0 }}</strong></div>
      <div class="metric"><span>运行模式</span><strong>SHADOW</strong></div>
    </div>
    <div v-else class="summary-grid">
      <div class="metric"><span>市场情绪</span><strong>{{ emotionText }}</strong><small>{{ score(summary?.market_emotion_score) }} 分</small></div>
      <div class="metric"><span>市场状态</span><strong>{{ summary?.market_regime ?? "暂无" }}</strong></div>
      <div class="metric"><span>候选数</span><strong>{{ summary?.candidate_count ?? 0 }}</strong></div>
      <div class="metric pass"><span>通过</span><strong>{{ summary?.pass_count ?? 0 }}</strong></div>
      <div class="metric review"><span>观察</span><strong>{{ summary?.review_count ?? 0 }}</strong></div>
      <div class="metric block"><span>阻断</span><strong>{{ summary?.block_count ?? 0 }}</strong></div>
    </div>

    <div v-if="version === 'V2.2'" class="filters">
      <el-segmented v-model="v22Filters.pool_type" :options="poolOptions" @change="resetAndLoad" />
      <el-select v-model="v22Filters.regime_state" clearable placeholder="全部市场状态" @change="resetAndLoad">
        <el-option v-for="item in v22Regimes" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="v22Filters.deployment_status" clearable placeholder="全部部署状态" @change="resetAndLoad">
        <el-option v-for="item in deploymentOptions" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="v22Filters.crowding_status" clearable placeholder="全部集中状态" @change="resetAndLoad">
        <el-option v-for="item in crowdingOptions" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="v22Filters.trigger_status" clearable placeholder="全部触发状态" @change="resetAndLoad">
        <el-option v-for="item in triggerOptions" :key="item" :label="item" :value="item" />
      </el-select>
    </div>
    <div v-else class="filters">
      <el-segmented v-model="filters.pool_type" :options="poolOptions" @change="resetAndLoad" />
      <el-select v-model="filters.strategy_id" clearable placeholder="全部策略" @change="resetAndLoad">
        <el-option v-for="item in strategyOptions" :key="item" :label="strategyText(item)" :value="item" />
      </el-select>
      <el-select v-model="filters.emotion_state" clearable placeholder="全部情绪" @change="resetAndLoad">
        <el-option v-for="item in emotionOptions" :key="item" :label="emotionLabel(item)" :value="item" />
      </el-select>
      <el-select v-model="filters.admission_status_v1" clearable placeholder="V1 全部状态" @change="resetAndLoad">
        <el-option v-for="item in statusOptions" :key="item" :label="statusText(item)" :value="item" />
      </el-select>
      <el-select v-model="filters.admission_status_v2" clearable placeholder="V2.1 全部状态" @change="resetAndLoad">
        <el-option v-for="item in statusOptions" :key="item" :label="statusText(item)" :value="item" />
      </el-select>
    </div>

    <div v-if="version === 'V2.2' && v22Summary" class="audit">
      <span>市场状态、行业集中与盘中触发均为 Shadow</span>
      <span>LLM {{ v22Summary.llm_calls }} · 外部 API {{ v22Summary.external_calls }} · 订单 {{ v22Summary.orders_created }}</span>
    </div>
    <div v-else-if="summary" class="audit">
      <span>版本 {{ summary.versions?.entry_timing_version ?? "entry_timing_v2_1" }}</span>
      <span>Quant / Flash / Pro 未改动：{{ hashesUnchanged ? "是" : "否" }}</span>
      <span>LLM {{ summary.llm_calls }} · 外部 API {{ summary.external_api_calls }} · 订单 {{ summary.order_creation_count ?? 0 }}</span>
    </div>

    <el-empty v-if="!loading && hasSummary && total === 0" description="当前筛选条件下无准入候选" />
    <CenteredDataTable v-else :rows="tableRows" :columns="columns" :loading="loading" :total="total"
      :current-page="page" :page-size="pageSize" height="560" @pagination-change="changePage" />
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref, watch } from "vue";
import {
  getEntryTimingV2Results, getEntryTimingV22Results, getLatestEntryTimingV2, getLatestEntryTimingV22,
  runEntryTimingV2Shadow, runEntryTimingV22Historical,
} from "@/api/entryTiming";
import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import { useWorkbenchStore } from "@/stores/workbench";
import type { EntryTimingFilters, EntryTimingRow, EntryTimingSummary, EntryTimingV22Filters, EntryTimingV22Row, EntryTimingV22Summary } from "@/types/entryTiming";

const store = useWorkbenchStore();
const version = ref<"V2.1" | "V2.2">("V2.2");
const versionOptions = ["V2.1", "V2.2"];
const summary = ref<EntryTimingSummary | null>(null);
const v22Summary = ref<EntryTimingV22Summary | null>(null);
const rows = ref<EntryTimingRow[]>([]);
const v22Rows = ref<EntryTimingV22Row[]>([]);
const total = ref(0); const page = ref(1); const pageSize = ref(50);
const loading = ref(false); const running = ref(false);
const filters = reactive<EntryTimingFilters>({ pool_type: "AI_POOL" });
const v22Filters = reactive<EntryTimingV22Filters>({ pool_type: "AI_POOL" });
const poolOptions = [{ label: "模型候选池", value: "AI_POOL" }, { label: "人工挑战池", value: "MANUAL_CHALLENGE_POOL" }];
const strategyOptions = ["TREND_BREAKOUT", "STRONG_PULLBACK", "SECTOR_RESONANCE", "OVERSOLD_REBOUND", "UNCLASSIFIED"];
const emotionOptions = ["GREEN", "YELLOW", "RED", "DATA_INSUFFICIENT"];
const statusOptions = ["PASS", "REVIEW", "BLOCK", "DATA_INSUFFICIENT"];
const v22Regimes = ["RISK_ON", "ROTATION", "REPAIR", "RISK_OFF", "CRASH"];
const deploymentOptions = ["DEPLOYABLE", "WATCH", "BLOCK_NEW_LONG", "REMOVED_BY_REGIME"];
const crowdingOptions = ["RETAINED", "SECTOR_CROWDING_REVIEW", "NOT_EVALUATED"];
const triggerOptions = ["ENTRY_TRIGGERED", "WAITING_TRIGGER", "DATA_INSUFFICIENT", "NOT_ELIGIBLE"];
const strategyNames: Record<string, string> = { TREND_BREAKOUT: "趋势突破", STRONG_PULLBACK: "强势回踩", SECTOR_RESONANCE: "板块共振", OVERSOLD_REBOUND: "超跌反弹", UNCLASSIFIED: "未分类" };
const emotionNames: Record<string, string> = { GREEN: "偏强", YELLOW: "中性", RED: "偏弱", DATA_INSUFFICIENT: "数据不足" };
const statusNames: Record<string, string> = { PASS: "通过", REVIEW: "观察", BLOCK: "阻断", DATA_INSUFFICIENT: "数据不足" };
const score = (value: unknown) => typeof value === "number" ? value.toFixed(2) : "-";
const percent = (value: unknown) => typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "-";
const list = (value: unknown) => Array.isArray(value) && value.length ? value.join("、") : "无";
const strategyText = (value: unknown) => strategyNames[String(value)] || String(value);
const emotionLabel = (value: unknown) => emotionNames[String(value)] || String(value);
const statusText = (value: unknown) => statusNames[String(value)] || String(value);
const emotionText = computed(() => emotionLabel(summary.value?.market_emotion_state ?? "DATA_INSUFFICIENT"));
const hashesUnchanged = computed(() => Boolean(summary.value?.quant_hash_unchanged && summary.value?.flash_hash_unchanged && summary.value?.pro_hash_unchanged));
const hasSummary = computed(() => version.value === "V2.2" ? Boolean(v22Summary.value) : Boolean(summary.value));
const tableRows = computed(() => version.value === "V2.2" ? v22Rows.value : rows.value);
const v21Columns = [
  { key: "quant_rank", label: "Quant排名" }, { key: "stock_code", label: "股票代码" }, { key: "stock_name", label: "股票名称" },
  { key: "strategy_id", label: "策略分类", formatter: strategyText }, { key: "entry_timing_v2_score", label: "时机V2.1", formatter: score },
  { key: "admission_ranking_score_v2", label: "准入排序", formatter: score }, { key: "admission_status_v1", label: "准入V1", formatter: statusText },
  { key: "admission_status_v2", label: "准入V2.1", formatter: statusText }, { key: "review_reasons", label: "观察原因", formatter: list, minWidth: 190 },
];
const v22Columns = [
  { key: "stock_code", label: "股票代码" }, { key: "stock_name", label: "股票名称" }, { key: "pool_type", label: "候选池" },
  { key: "strategy_id", label: "策略", formatter: strategyText }, { key: "admission_score", label: "准入分", formatter: score },
  { key: "regime_state", label: "市场状态" }, { key: "deployment_status", label: "部署状态", minWidth: 130 },
  { key: "position_multiplier", label: "仓位乘数", formatter: percent }, { key: "industry", label: "行业" },
  { key: "crowding_status", label: "行业集中", minWidth: 155 }, { key: "retained_rank_in_sector", label: "行业内保留排名" },
  { key: "industry_candidate_count_before", label: "行业门禁前" }, { key: "industry_candidate_count_after", label: "行业门禁后" },
  { key: "trigger_status", label: "盘中触发", minWidth: 145 }, { key: "trigger_reasons_json", label: "触发原因", formatter: list, minWidth: 210 },
];
const columns = computed(() => version.value === "V2.2" ? v22Columns : v21Columns);

async function loadRows() {
  if (version.value === "V2.2") {
    if (!v22Summary.value?.run_id) { v22Rows.value = []; total.value = 0; return; }
    const response = await getEntryTimingV22Results(v22Summary.value.run_id, page.value, pageSize.value, { ...v22Filters });
    v22Rows.value = response.data.items; total.value = response.data.total;
  } else {
    if (!summary.value?.run_id) { rows.value = []; total.value = 0; return; }
    const response = await getEntryTimingV2Results(summary.value.run_id, page.value, pageSize.value, { ...filters });
    rows.value = response.data.items; total.value = response.data.total;
  }
}
async function refresh() {
  loading.value = true;
  try {
    if (version.value === "V2.2") v22Summary.value = (await getLatestEntryTimingV22(store.tradeDate)).data;
    else summary.value = (await getLatestEntryTimingV2(store.tradeDate)).data;
    await loadRows();
  } finally { loading.value = false; }
}
async function runShadow() {
  try {
    await ElMessageBox.confirm(`仅生成 ${version.value} 影子结果，不改变正式推荐。确认继续？`, "运行影子分析", { type: "info", confirmButtonText: "确认运行", cancelButtonText: "取消" });
    running.value = true;
    if (version.value === "V2.2") await runEntryTimingV22Historical(store.tradeDate, store.tradeDate);
    else summary.value = (await runEntryTimingV2Shadow(store.tradeDate)).data;
    page.value = 1; await refresh(); ElMessage.success(`${version.value} 影子分析已完成`);
  } catch (error) { if (error !== "cancel") ElMessage.error(error instanceof Error ? error.message : "运行失败"); }
  finally { running.value = false; }
}
function resetAndLoad() { page.value = 1; void loadRows(); }
function resetAndRefresh() { page.value = 1; total.value = 0; void refresh(); }
function changePage(value: { page: number; pageSize: number }) { page.value = value.page; pageSize.value = value.pageSize; void loadRows(); }
watch(() => store.tradeDate, () => { void refresh(); });
onMounted(refresh);
</script>

<style scoped>
.entry-page { display: grid; gap: 14px; }
.page-head, .actions, .audit, .filters { display: flex; align-items: center; gap: 12px; }
.page-head { justify-content: space-between; } h1 { margin: 0; color: #182536; font-size: 24px; letter-spacing: 0; } p { margin: 6px 0 0; color: #6c7887; }
.summary-grid { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); border: 1px solid #dce3ea; background: #fff; }
.metric { min-height: 82px; display: flex; flex-direction: column; justify-content: center; padding: 0 16px; border-right: 1px solid #e6ebf0; }
.metric:last-child { border-right: 0; } .metric span, .metric small { color: #758293; font-size: 12px; } .metric strong { margin-top: 5px; color: #172333; font-size: 20px; }
.metric.pass strong { color: #28764a; } .metric.review strong { color: #9b6a09; } .metric.block strong { color: #b33a3a; }
.audit { min-height: 38px; flex-wrap: wrap; color: #687586; font-size: 12px; } .filters { flex-wrap: wrap; } .filters .el-select { width: 158px; }
@media (max-width: 1000px) { .summary-grid { grid-template-columns: repeat(3, 1fr); } .metric:nth-child(3n) { border-right: 0; } }
@media (max-width: 680px) { .page-head, .actions { align-items: flex-start; flex-direction: column; } .summary-grid { grid-template-columns: repeat(2, 1fr); } .metric:nth-child(3n) { border-right: 1px solid #e6ebf0; } .metric:nth-child(even) { border-right: 0; } .filters .el-select { width: calc(50% - 6px); } }
</style>
