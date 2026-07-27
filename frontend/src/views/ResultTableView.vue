<template>
  <section>
    <div class="page-title">
      <div><h1>{{ title }}</h1><p>{{ subtitle }}</p></div>
      <div class="actions">
        <el-button v-if="monitorEnabled && auth.canWrite" type="primary" :disabled="!selectedRows.length" @click="addSelectedToMonitor">加入盯盘</el-button>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>
    <el-alert
      v-if="props.kind === 'quant'"
      title="当前正式 Quant 分数属于 TUSHARE_BASELINE_V1 Legacy 未校准分数，不等同于收益概率；比较时应优先查看排名。研究 Shadow 版本不会自动替换正式结果。"
      type="info"
      show-icon
      :closable="false"
      class="legacy-score-note"
    />
    <el-alert v-if="store.status?.source_mode === 'EMPTY'" title="该交易日没有已完成的正式流水线结果。" type="info" show-icon :closable="false" />
    <CenteredDataTable
      v-else
      :rows="rows"
      :columns="columns"
      :loading="loading"
      :pagination-enabled="paginated"
      :total="paginated ? total : undefined"
      :current-page="query.page"
      :page-size="query.pageSize"
      :selectable="monitorEnabled && auth.canWrite"
      @pagination-change="handlePaginationChange"
      @selection-change="handleSelection"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";

import { workbenchApi } from "@/api/workbench";
import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import { useMonitorPool } from "@/composables/useMonitorPool";
import { useWorkbenchStore } from "@/stores/workbench";
import { useInternalAuthStore } from "@/stores/internalAuth";
import type { MonitorPoolCandidate } from "@/types/realtime";
import type { TableColumn } from "@/types/workbench";

const props = defineProps<{ kind: "quant" | "flash" | "final" | "orders" | "fundamentals" }>();
const store = useWorkbenchStore();
const auth = useInternalAuthStore();
const rows = ref<Record<string, unknown>[]>([]);
const total = ref(0);
const query = reactive({ page: 1, pageSize: 50, keyword: "", sortBy: "rank", sortOrder: "asc" });
const loading = ref(false);
const selectedRows = ref<Record<string, unknown>[]>([]);
const { addToMonitor } = useMonitorPool();
let sequence = 0;
let controller: AbortController | null = null;

const titles = { quant: "全 A 量化排名", flash: "LLM 二筛评分", final: "最终排序", orders: "挂单与仓位", fundamentals: "重点基本面" };
const subtitles = { quant: "读取当前 Quant Run 的完整服务端分页结果。", flash: "包含成功、失败、Top20、人工和最终候选标记。", final: "读取当前 Candidate Set 对应的 Pro V3 连续排名。", orders: "规则挂单与建议仓位，仅作辅助分析。", fundamentals: "保留候选股基本面结果及股票级失败标记。" };
const title = computed(() => titles[props.kind]);
const subtitle = computed(() => subtitles[props.kind]);
const paginated = computed(() => ["quant", "flash"].includes(props.kind));
const monitorEnabled = computed(() => ["final", "orders"].includes(props.kind));
const columns = computed<TableColumn[]>(() => columnMaps[props.kind]);

const columnMaps: Record<string, TableColumn[]> = {
  quant: ["rank|量化排名", "stock_code|股票代码", "stock_name|股票名称", "total_score|量化总分", "technical_score|技术分", "capital_score|资金分", "emotion_score|情绪分", "momentum_score|动量分", "risk_score|风险分", "manual_selected|人工选择"].map(parseColumn),
  flash: ["rank|二筛排名", "stock_code|股票代码", "stock_name|股票名称", "quant_score|量化分数", "flash_score|Flash 分数", "decision|二筛结论", "confidence|置信度", "llm_selected|Top20", "manual_selected|人工选择", "execution_status|执行状态", "error_category|错误类别"].map(parseColumn),
  final: ["final_rank|最终排名", "stock_code|股票代码", "stock_name|股票名称", "source|来源", "pro_score|Pro 评分", "pro_priority|优先级", "flash_score|Flash 评分", "flash_decision|Flash 结论", "quant_rank|量化排名", "financial_status|财务状态", "summary|最终摘要"].map(parseColumn),
  orders: ["final_rank|最终排名", "stock_code|股票代码", "stock_name|股票名称", "source|来源", "recommended_price|推荐价", "stop_loss_price|止损价", "take_profit_1|止盈 1", "take_profit_2|止盈 2", "risk_reward|风险收益比", "position_percent|建议仓位", "suggested_capital|建议资金", "suggested_quantity|建议股数", "max_loss|最大损失", "status|状态", "warnings|风险警告"].map(parseColumn),
  fundamentals: ["final_rank|最终排名", "stock_code|股票代码", "stock_name|股票名称", "source|来源", "analysis_status_label|补全状态", "research_mode_label|资料模式", "industry_chain|产业链", "chain_position_label|产业链环节", "main_business|主营业务", "core_products|核心产品", "concept_tags|概念标签", "investment_logic|投资逻辑", "financial_status_label|财务状态", "financial_summary|最新财务摘要", "key_risks|主要风险", "evidence_count|证据数", "evidence_sources|核验来源"].map(parseColumn)
};

function parseColumn(value: string): TableColumn { const [key, label] = value.split("|"); return { key, label, minWidth: ["summary", "warnings", "main_business", "investment_logic", "financial_summary", "key_risks", "evidence_sources"].includes(key) ? 260 : 120 }; }

async function load() {
  const current = ++sequence;
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  const selectedDate = store.tradeDate;
  const runId = store.status?.pipeline_run_id;
  try {
    const response = props.kind === "quant" ? await workbenchApi.quant(selectedDate, query.page, query.pageSize, runId, controller.signal)
      : props.kind === "flash" ? await workbenchApi.flash(selectedDate, query.page, query.pageSize, runId, controller.signal)
      : props.kind === "final" ? await workbenchApi.final(selectedDate, runId, controller.signal)
      : props.kind === "orders" ? await workbenchApi.orders(selectedDate, runId, controller.signal)
      : await workbenchApi.fundamentals(selectedDate, runId, controller.signal);
    if (current !== sequence || selectedDate !== store.tradeDate) return;
    rows.value = response.data.items;
    total.value = "total" in response.data ? Number(response.data.total) : response.data.items.length;
  } catch { /* Keep the last successful page visible. */ }
  finally { if (current === sequence) loading.value = false; }
}
function handlePaginationChange(payload: { page: number; pageSize: number }) {
  query.page = payload.page;
  query.pageSize = payload.pageSize;
  void load();
}
function handleSelection(value: Record<string, unknown>[]) { selectedRows.value = value; }
async function addSelectedToMonitor() {
  const source = props.kind === "orders" ? "ACTIVE_ORDER_PLAN" : "FINAL_CANDIDATE";
  const candidates: MonitorPoolCandidate[] = selectedRows.value.map((row) => ({
    stock_code: String(row.stock_code || ""),
    stock_name: String(row.stock_name || ""),
    sources: [source],
    monitor_profile: "CANDIDATE_MONITOR",
    priority: props.kind === "orders" ? "HIGH" : "NORMAL",
    recommended_price: numberOrNull(row.recommended_price),
    stop_loss: numberOrNull(row.stop_loss_price),
    take_profit_1: numberOrNull(row.take_profit_1),
    take_profit_2: numberOrNull(row.take_profit_2)
  }));
  await addToMonitor(store.tradeDate, candidates, source, store.status?.pipeline_run_id || undefined);
}
function numberOrNull(value: unknown): number | null { const result = Number(value); return Number.isFinite(result) ? result : null; }
watch(() => [store.tradeDate, store.status?.pipeline_run_id, props.kind], () => { query.page = 1; void load(); }, { immediate: true });
onBeforeUnmount(() => controller?.abort());
</script>

<style scoped>
.page-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.page-title h1 { margin: 0; font-size: 22px; }
.page-title p { margin: 5px 0 0; color: #667085; font-size: 13px; }
.actions { display: flex; align-items: center; gap: 8px; }
.legacy-score-note { margin-bottom: 12px; }
</style>
