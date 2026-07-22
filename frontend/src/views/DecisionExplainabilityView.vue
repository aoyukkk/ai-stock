<template>
  <section class="explain-page">
    <header class="page-head">
      <div>
        <h1>Decision Explainability</h1>
        <p>解释为什么选中、为什么拒绝，以及门禁取消后的 Shadow 反事实变化。</p>
      </div>
      <el-button :loading="loading" @click="refresh">刷新只读结果</el-button>
    </header>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="仅展示 Admission V3 Shadow；不修改 Quant、Entry Timing V2.2、Flash/Pro Prompt、生产推荐或交易配置。"
    />

    <div class="summary-grid">
      <div class="metric"><span>候选</span><strong>{{ summary?.candidate_count ?? 0 }}</strong></div>
      <div class="metric pass"><span>PASS_CORE</span><strong>{{ summary?.pass_core_count ?? 0 }}</strong></div>
      <div class="metric pass"><span>PASS_EXPLORATORY</span><strong>{{ summary?.pass_exploratory_count ?? 0 }}</strong></div>
      <div class="metric review"><span>REVIEW</span><strong>{{ summary?.review_count ?? 0 }}</strong></div>
      <div class="metric reject"><span>REJECT</span><strong>{{ summary?.reject_count ?? 0 }}</strong></div>
      <div class="metric"><span>OPEN_SET</span><strong>{{ summary?.open_set_count ?? 0 }}</strong></div>
    </div>

    <div v-if="summary" class="audit">
      <span>Quant / Flash / Pro Hash：{{ hashesUnchanged ? "未变化" : "异常" }}</span>
      <span>LLM {{ summary.llm_calls }} · 外部 API {{ summary.external_api_calls }} · 订单 {{ summary.orders_created }}</span>
      <span>{{ summary.version }} · SHADOW ONLY</span>
    </div>

    <div class="value-cards">
      <div><span>累计避免亏损</span><strong>{{ formatReturnValue(totalAvoidedLoss) }}</strong></div>
      <div><span>累计误杀机会</span><strong>{{ formatReturnValue(totalMissedGain) }}</strong></div>
    </div>

    <div class="filters">
      <el-select v-model="admissionState" clearable placeholder="全部准入状态" @change="resetAndLoad">
        <el-option v-for="item in admissionStates" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="strategyStatus" clearable placeholder="全部分类状态" @change="resetAndLoad">
        <el-option label="PROBABILISTIC" value="PROBABILISTIC" />
        <el-option label="OPEN_SET" value="OPEN_SET" />
      </el-select>
      <span class="hint">双击一行查看时间契约、完整因子归因和逐门禁反事实。</span>
    </div>

    <el-empty v-if="!loading && !summary" description="该交易日暂无 Admission V3 Shadow 结果" />
    <CenteredDataTable
      v-else
      :rows="rows"
      :columns="columns"
      :loading="loading"
      :total="total"
      :current-page="page"
      :page-size="pageSize"
      height="470"
      @pagination-change="changePage"
      @row-double-click="openDetail"
    />

    <section v-if="factorPerformance.length" class="gate-section">
      <h2>因子长期表现</h2>
      <p>只使用已成熟的真实持仓日收益，不改变任何评分权重。</p>
      <el-table :data="factorPerformance" border stripe>
        <el-table-column prop="factor_family" label="因子族" align="center" min-width="170" />
        <el-table-column prop="sample_count" label="样本数" align="center" />
        <el-table-column prop="win_rate" label="胜率" align="center" :formatter="formatPercentCell" />
        <el-table-column prop="avg_return_d1" label="D1平均收益" align="center" :formatter="formatReturn" />
        <el-table-column prop="avg_return_d3" label="D3平均收益" align="center" :formatter="formatReturn" />
        <el-table-column prop="avg_return_d5" label="D5平均收益" align="center" :formatter="formatReturn" />
        <el-table-column prop="avg_drawdown" label="平均回撤" align="center" :formatter="formatReturn" />
        <el-table-column prop="positive_contribution_rate" label="正贡献率" align="center" :formatter="formatPercentCell" />
        <el-table-column prop="negative_contribution_rate" label="负贡献率" align="center" :formatter="formatPercentCell" />
      </el-table>
    </section>

    <section v-if="gateRanking.length" class="gate-section">
      <h2>Gate 价值排名</h2>
      <p>同时展示误杀机会、避免亏损和净门禁价值；无成熟收益时保持空值。</p>
      <el-table :data="gateRanking" border stripe>
        <el-table-column prop="gate_name" label="Gate" align="center" min-width="180" />
        <el-table-column prop="trigger_count" label="触发数" align="center" />
        <el-table-column prop="blocked_count" label="阻止数" align="center" />
        <el-table-column prop="future_return" label="触发后收益" align="center" :formatter="formatReturn" />
        <el-table-column prop="avoided_loss" label="避免亏损" align="center" :formatter="formatReturn" />
        <el-table-column prop="missed_gain" label="误杀机会" align="center" :formatter="formatReturn" />
        <el-table-column prop="net_gate_value" label="净门禁价值" align="center" :formatter="formatReturn" />
        <el-table-column prop="false_positive_rate" label="误杀率" align="center" :formatter="formatPercentCell" />
      </el-table>
    </section>

    <el-drawer v-model="drawerOpen" size="72%" title="逐股决策解释">
      <template v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="股票">{{ detail.stock_code }} {{ detail.stock_name }}</el-descriptions-item>
          <el-descriptions-item label="Admission V3">{{ detail.admission_state }}</el-descriptions-item>
          <el-descriptions-item label="为什么选中">{{ detail.why_selected || "-" }}</el-descriptions-item>
          <el-descriptions-item label="为什么拒绝">{{ detail.why_rejected?.join("、") || "-" }}</el-descriptions-item>
          <el-descriptions-item label="最大贡献因子">{{ detail.largest_factor || "-" }}</el-descriptions-item>
          <el-descriptions-item label="影响最大门禁">{{ detail.largest_gate || "-" }}</el-descriptions-item>
          <el-descriptions-item label="时间契约" :span="2">{{ timingContractText }}</el-descriptions-item>
        </el-descriptions>

        <h3>Factor Attribution</h3>
        <el-table :data="detail.factor_attribution" border stripe>
          <el-table-column prop="factor_family" label="因子族" align="center" min-width="160" />
          <el-table-column prop="normalized_score" label="标准分" align="center" :formatter="formatNumberCell" />
          <el-table-column prop="score_contribution" label="评分贡献" align="center" :formatter="formatNumberCell" />
          <el-table-column prop="gate_contribution" label="门禁贡献" align="center" :formatter="formatNumberCell" />
          <el-table-column prop="rank_contribution" label="排名贡献" align="center" :formatter="formatNumberCell" />
          <el-table-column prop="interaction_note" label="交互说明" align="center" min-width="260" />
        </el-table>

        <h3>取消门禁后的变化</h3>
        <el-table :data="counterfactualRows" border stripe>
          <el-table-column prop="gate" label="取消门禁" align="center" min-width="180" />
          <el-table-column prop="admission_state" label="新状态" align="center" />
          <el-table-column prop="score_delta" label="分数变化" align="center" :formatter="formatNumberCell" />
          <el-table-column prop="position_multiplier" label="仓位系数" align="center" :formatter="formatNumberCell" />
        </el-table>
      </template>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onMounted, ref, watch } from "vue";

import {
  getDecisionExplainabilityDetail,
  getDecisionExplainabilityResults,
  getGateValueRanking,
  getLatestFactorPerformance,
  getLatestDecisionExplainability,
} from "@/api/decisionExplainability";
import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import { useWorkbenchStore } from "@/stores/workbench";
import type {
  DecisionExplainabilityDetail,
  DecisionExplainabilityRow,
  DecisionExplainabilitySummary,
  FactorPerformanceRow,
  GateValueRow,
} from "@/types/decisionExplainability";

const store = useWorkbenchStore();
const summary = ref<DecisionExplainabilitySummary | null>(null);
const rows = ref<DecisionExplainabilityRow[]>([]);
const factorPerformance = ref<FactorPerformanceRow[]>([]);
const gateRanking = ref<GateValueRow[]>([]);
const detail = ref<DecisionExplainabilityDetail | null>(null);
const loading = ref(false);
const drawerOpen = ref(false);
const total = ref(0);
const page = ref(1);
const pageSize = ref(50);
const admissionState = ref("");
const strategyStatus = ref("");
const admissionStates = ["PASS_CORE", "PASS_EXPLORATORY", "REVIEW", "REJECT"];

const hashesUnchanged = computed(() => Boolean(
  summary.value?.quant_hash_unchanged && summary.value?.flash_hash_unchanged && summary.value?.pro_hash_unchanged,
));
const counterfactualRows = computed(() => Object.entries(detail.value?.counterfactuals || {}).map(([gate, value]) => ({ gate, ...value })));
const timingContractText = computed(() => {
  const contract = detail.value?.timing_contract;
  if (!contract) return "-";
  return `${contract.observation_end_ts} ≤ ${contract.available_at_ts} ≤ ${contract.signal_generated_at} < ${contract.order_eligible_at}`;
});
const totalAvoidedLoss = computed(() => gateRanking.value.reduce((total, row) => total + row.avoided_loss, 0));
const totalMissedGain = computed(() => gateRanking.value.reduce((total, row) => total + row.missed_gain, 0));

const score = (value: unknown) => typeof value === "number" ? value.toFixed(2) : "-";
const percent = (value: unknown) => typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "-";
const probability = (value: unknown) => {
  if (!value || typeof value !== "object") return "-";
  return Object.entries(value as Record<string, number>)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([key, item]) => `${key} ${(item * 100).toFixed(1)}%`)
    .join(" / ");
};
const reason = (_value: unknown, row: Record<string, unknown>) => {
  const selected = String(row.why_selected || "");
  const rejected = Array.isArray(row.why_rejected) ? row.why_rejected.join("、") : "";
  return selected || rejected || "-";
};
const counterfactual = (value: unknown) => {
  if (!value || typeof value !== "object" || !Object.keys(value).length) return "-";
  return Object.entries(value as Record<string, { admission_state: string; score_delta: number }>)
    .map(([gate, item]) => `${gate}: ${item.admission_state} (${item.score_delta >= 0 ? "+" : ""}${item.score_delta.toFixed(1)})`)
    .join("；");
};
const columns = [
  { key: "quant_rank", label: "Quant 排名" },
  { key: "stock_code", label: "股票代码" },
  { key: "stock_name", label: "股票名称" },
  { key: "admission_state", label: "Admission V3", minWidth: 150 },
  { key: "strategy_status", label: "策略状态", minWidth: 135 },
  { key: "strategy_probability", label: "策略概率", formatter: probability, minWidth: 260 },
  { key: "final_score", label: "Shadow 分", formatter: score },
  { key: "expected_value_score", label: "EV", formatter: score },
  { key: "risk_adjusted_opportunity_score", label: "风险调整机会", formatter: score, minWidth: 145 },
  { key: "position_multiplier", label: "仓位系数", formatter: percent },
  { key: "largest_factor", label: "最大贡献因子", minWidth: 170 },
  { key: "largest_gate", label: "最大影响门禁", minWidth: 170 },
  { key: "why_selected", label: "选中 / 拒绝原因", formatter: reason, minWidth: 300 },
  { key: "counterfactuals", label: "取消门禁后的变化", formatter: counterfactual, minWidth: 320 },
];

async function loadRows() {
  const [factorResponse, gateResponse] = await Promise.all([
    getLatestFactorPerformance(store.tradeDate),
    getGateValueRanking(store.tradeDate),
  ]);
  factorPerformance.value = factorResponse.data;
  gateRanking.value = gateResponse.data;
  if (!summary.value?.run_id) {
    rows.value = [];
    total.value = 0;
    return;
  }
  const resultResponse = await getDecisionExplainabilityResults(
    summary.value.run_id, page.value, pageSize.value, admissionState.value, strategyStatus.value,
  );
  rows.value = resultResponse.data.items;
  total.value = resultResponse.data.total;
}

async function refresh() {
  loading.value = true;
  try {
    summary.value = (await getLatestDecisionExplainability(store.tradeDate)).data;
    await loadRows();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "读取解释结果失败");
  } finally {
    loading.value = false;
  }
}

async function openDetail(row: Record<string, unknown>) {
  if (!summary.value?.run_id || !row.stock_code) return;
  detail.value = (await getDecisionExplainabilityDetail(summary.value.run_id, String(row.stock_code))).data;
  drawerOpen.value = true;
}

function resetAndLoad() { page.value = 1; void loadRows(); }
function changePage(value: { page: number; pageSize: number }) { page.value = value.page; pageSize.value = value.pageSize; void loadRows(); }
function formatNumberCell(_row: unknown, _column: unknown, value: unknown) { return score(value); }
function formatReturn(_row: unknown, _column: unknown, value: unknown) { return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "待收益成熟"; }
function formatPercentCell(_row: unknown, _column: unknown, value: unknown) { return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "-"; }
function formatReturnValue(value: number) { return `${(value * 100).toFixed(2)}%`; }

watch(() => store.tradeDate, refresh);
onMounted(refresh);
</script>

<style scoped>
.explain-page { display: grid; gap: 14px; }
.page-head, .filters, .audit { display: flex; align-items: center; gap: 12px; }
.page-head { justify-content: space-between; }
h1 { margin: 0; color: #182536; font-size: 24px; }
h2, h3 { margin: 16px 0 8px; color: #24364b; }
p { margin: 6px 0 0; color: #6c7887; }
.summary-grid { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); border: 1px solid #dce3ea; background: #fff; }
.metric { min-height: 82px; display: flex; flex-direction: column; justify-content: center; padding: 0 16px; border-right: 1px solid #e6ebf0; }
.metric:last-child { border-right: 0; }
.metric span { color: #758293; font-size: 12px; }
.metric strong { margin-top: 5px; color: #172333; font-size: 20px; }
.metric.pass strong { color: #28764a; }
.metric.review strong { color: #9b6a09; }
.metric.reject strong { color: #b33a3a; }
.audit { min-height: 34px; flex-wrap: wrap; color: #687586; font-size: 12px; }
.value-cards { display: grid; grid-template-columns: repeat(2, minmax(180px, 260px)); gap: 12px; }
.value-cards > div { padding: 12px 16px; border: 1px solid #dce3ea; background: #fff; }
.value-cards span, .value-cards strong { display: block; }
.value-cards span { color: #758293; font-size: 12px; }
.value-cards strong { margin-top: 4px; color: #174f87; font-size: 20px; }
.filters { flex-wrap: wrap; }
.filters .el-select { width: 180px; }
.hint { color: #7a8795; font-size: 12px; }
.gate-section { padding: 14px; border: 1px solid #dce3ea; background: #fff; }
.gate-section h2 { margin-top: 0; }
@media (max-width: 1000px) { .summary-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 680px) { .page-head { align-items: flex-start; flex-direction: column; } .summary-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
