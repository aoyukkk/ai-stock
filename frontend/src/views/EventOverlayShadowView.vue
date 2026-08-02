<template>
  <section class="event-overlay-page">
    <el-card shadow="never">
      <template #header>
        <div class="heading">
          <div>
            <h2>V3 Event Overlay Shadow</h2>
            <p>只读研究页：事件仅影响第二轮筛选，不修改 V2 Quant、价格、仓位或订单。</p>
          </div>
          <el-tag type="warning">SHADOW</el-tag>
        </div>
      </template>
      <el-alert
        title="DeepSeek V4 Flash 直搜属于显式降级证据源，不能用于生产 Hard Gate。"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-descriptions v-if="summary" :column="3" border class="summary">
        <el-descriptions-item label="Run ID">{{ summary.run_id }}</el-descriptions-item>
        <el-descriptions-item label="决策时点">{{ summary.decision_as_of_time }}</el-descriptions-item>
        <el-descriptions-item label="Market Regime">{{ marketRegime }}</el-descriptions-item>
        <el-descriptions-item label="输入数量">{{ summary.input_count }}</el-descriptions-item>
        <el-descriptions-item label="真实调用">{{ summary.actual_network_calls }}</el-descriptions-item>
        <el-descriptions-item label="Checkpoint复用">{{ summary.reused_checkpoint_count }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never" class="table-card">
      <template #header><strong>V2 / V3 排名变化与事件证据</strong></template>
      <el-table :data="rows" stripe border v-loading="loading">
        <el-table-column prop="v3_rank" label="V3排名" width="78" />
        <el-table-column prop="stock_code" label="股票代码" width="100" />
        <el-table-column prop="stock_name" label="股票名称" width="120" />
        <el-table-column prop="quant_rank" label="Quant排名" width="94" />
        <el-table-column prop="v2_screening_rank" label="V2初筛" width="88" />
        <el-table-column prop="rank_change" label="变化" width="72" />
        <el-table-column prop="event_opportunity_score" label="Event分" width="88" />
        <el-table-column prop="evidence_confidence" label="证据置信" width="94" />
        <el-table-column prop="risk_action" label="风险动作" width="105" />
        <el-table-column prop="search_status" label="搜索状态" min-width="220" />
        <el-table-column prop="selected_top20" label="Top20" width="74">
          <template #default="{ row }"><el-tag :type="row.selected_top20 ? 'success' : 'info'">{{ row.selected_top20 ? "是" : "否" }}</el-tag></template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="table-card">
      <template #header><strong>来源与发布时间</strong></template>
      <el-table :data="evidence" border>
        <el-table-column prop="event_type" label="事件类型" width="110" />
        <el-table-column prop="event_direction" label="方向" width="82" />
        <el-table-column prop="title" label="标题" min-width="220" />
        <el-table-column prop="source_tier" label="来源等级" width="96" />
        <el-table-column prop="domain" label="来源" width="150" />
        <el-table-column prop="published_at" label="发布时间" width="190" />
        <el-table-column prop="score_eligible" label="计入评分" width="92">
          <template #default="{ row }">
            <el-tag v-if="row.score_eligible === true" type="success">是</el-tag>
            <el-tag v-else-if="row.score_eligible === false" type="info">否</el-tag>
            <span v-else>历史未记录</span>
          </template>
        </el-table-column>
        <el-table-column prop="temporal_status" label="时效状态" width="180" />
        <el-table-column prop="freshness_window_hours" label="时效窗(h)" width="98" />
        <el-table-column label="不计分原因" min-width="190">
          <template #default="{ row }">
            {{ Array.isArray(row.score_exclusion_reasons) && row.score_exclusion_reasons.length
              ? row.score_exclusion_reasons.join("；")
              : "—" }}
          </template>
        </el-table-column>
        <el-table-column label="链接" width="90">
          <template #default="{ row }"><el-link v-if="row.url" :href="row.url" target="_blank">查看</el-link><span v-else>—</span></template>
        </el-table-column>
      </el-table>
    </el-card>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { eventOverlayApi } from "@/api/eventOverlay";
import { useWorkbenchStore } from "@/stores/workbench";

const store = useWorkbenchStore();
const loading = ref(false);
const summary = ref<Record<string, any> | null>(null);
const rows = ref<Record<string, any>[]>([]);
const evidence = ref<Record<string, any>[]>([]);
const marketRegime = computed(() => (summary.value?.market_regime as Record<string, unknown> | undefined)?.status || "沿用V2");

async function load() {
  if (!store.tradeDate) return;
  loading.value = true;
  try {
    const [summaryResult, comparisonResult, evidenceResult] = await Promise.all([
      eventOverlayApi.summary(store.tradeDate),
      eventOverlayApi.comparison(store.tradeDate),
      eventOverlayApi.evidence(store.tradeDate),
    ]);
    summary.value = summaryResult.data;
    rows.value = comparisonResult.data.items || [];
    evidence.value = evidenceResult.data.items || [];
  } finally {
    loading.value = false;
  }
}

watch(() => store.tradeDate, load);
onMounted(load);
</script>

<style scoped>
.event-overlay-page { display: grid; gap: 16px; }
.heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
h2 { margin: 0; color: #1b2b3d; }
p { margin: 6px 0 0; color: #667688; }
.summary { margin-top: 14px; }
.table-card :deep(.el-card__body) { padding: 0; }
</style>
