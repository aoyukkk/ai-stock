<template>
  <section class="effectiveness-page">
    <el-card shadow="never">
      <template #header>
        <div class="heading">
          <div>
            <h2>Full-Universe Quant Effectiveness</h2>
            <p>全A只读Shadow验证；股票数量不能替代成熟推荐日数量，结果不会修改评分、权重或版本。</p>
          </div>
          <el-tag type="warning">SHADOW / READ ONLY</el-tag>
        </div>
      </template>
      <div class="filters">
        <el-radio-group v-model="fullHorizon">
          <el-radio-button v-for="value in [1, 3, 5, 10]" :key="value" :value="value">
            D{{ value }}
          </el-radio-button>
        </el-radio-group>
      </div>
      <el-alert
        :title="`结论：${String(fullSummary.conclusion_status || 'NOT_MATURED')}；行业PIT不可验证时行业超额保持空值。`"
        type="warning"
        :closable="false"
        show-icon
      />
      <div class="cards">
        <div><span>全A Rank IC</span><strong>{{ fullHeadline("full_universe_rank_ic_mean") }}</strong></div>
        <div><span>全A Score IC</span><strong>{{ fullHeadline("full_universe_score_ic_mean") }}</strong></div>
        <div><span>十分位差</span><strong>{{ fullPercent("decile_spread_mean") }}</strong></div>
        <div><span>Top500-其余</span><strong>{{ fullPercent("top500_vs_rest_spread_mean") }}</strong></div>
        <div><span>成熟推荐日</span><strong>{{ fullHeadline("matured_day_count") }}</strong></div>
        <div><span>最大行情日期</span><strong>{{ fullSummary.maximum_market_data_date || "—" }}</strong></div>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header><strong>全A横截面分组</strong></template>
      <el-tabs>
        <el-tab-pane label="全A Rank IC">
          <el-table :data="fullIc" border>
            <el-table-column prop="ranking_trade_date" label="推荐日" width="110" />
            <el-table-column prop="metric_scope" label="范围" min-width="170" />
            <el-table-column prop="rank_ic" label="Rank IC" width="110" />
            <el-table-column prop="score_ic" label="Score IC" width="110" />
            <el-table-column prop="coverage_ratio" label="覆盖率" width="110" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="十分位收益">
          <el-table :data="fullDeciles" border>
            <el-table-column prop="ranking_trade_date" label="推荐日" width="110" />
            <el-table-column prop="group_name" label="分组" width="120" />
            <el-table-column prop="mean_return" label="平均收益" width="110" />
            <el-table-column prop="median_return" label="中位收益" width="110" />
            <el-table-column prop="coverage_ratio" label="覆盖率" width="100" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="固定500分组">
          <el-table :data="fullFixed" border>
            <el-table-column prop="ranking_trade_date" label="推荐日" width="110" />
            <el-table-column prop="group_name" label="分组" min-width="170" />
            <el-table-column prop="mean_return" label="平均收益" width="110" />
            <el-table-column prop="market_excess_return" label="市场超额" width="110" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="前排诊断">
          <el-table :data="fullHead" border>
            <el-table-column prop="ranking_trade_date" label="推荐日" width="110" />
            <el-table-column prop="group_name" label="分组" min-width="170" />
            <el-table-column prop="mean_return" label="平均收益" width="110" />
            <el-table-column prop="worst_return" label="最差收益" width="110" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="五因子IC">
          <el-table :data="fullFactorIc" border>
            <el-table-column prop="ranking_trade_date" label="推荐日" width="110" />
            <el-table-column prop="factor_name" label="因子" min-width="160" />
            <el-table-column prop="factor_score_ic" label="Score IC" width="120" />
            <el-table-column prop="coverage_ratio" label="覆盖率" width="110" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="heading">
          <div>
            <h2>Quant &amp; Flash Effectiveness</h2>
            <p>只读 Shadow 证据：每日先计算，再按排名日等权汇总；不修改评分、权重或晋级状态。</p>
          </div>
          <el-tag type="warning">SHADOW</el-tag>
        </div>
      </template>
      <div class="filters">
        <el-select v-model="screeningVersion" aria-label="版本选择器">
          <el-option v-for="version in screeningVersions" :key="version" :label="version" :value="version" />
        </el-select>
        <el-radio-group v-model="horizon">
          <el-radio-button v-for="value in [1, 3, 5, 10]" :key="value" :value="value">D{{ value }}</el-radio-button>
        </el-radio-group>
        <el-switch v-model="actionableOnly" active-text="仅开盘前可执行" />
      </div>
      <el-alert
        v-if="dataStatus !== 'NORMAL'"
        :title="`数据状态：${dataStatus}；Cohort 不一致时不显示增量结论。`"
        type="warning"
        :closable="false"
        show-icon
      />
      <div class="cards">
        <div><span>Quant Rank IC</span><strong>{{ metric("rank_ic") }}</strong></div>
        <div><span>Flash Score IC</span><strong>{{ metric("flash_score_ic") }}</strong></div>
        <div><span>Flash 增量</span><strong>{{ percent("incremental_lift") }}</strong></div>
        <div><span>Promote-Demote</span><strong>{{ percent("promote_demote_spread") }}</strong></div>
        <div><span>有效样本</span><strong>{{ latest?.valid_sample_count ?? "—" }}</strong></div>
        <div><span>可执行范围</span><strong>{{ actionableOnly ? "是" : "研究全样本" }}</strong></div>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header><strong>每日指标时间序列</strong></template>
      <el-table :data="daily" border v-loading="loading">
        <el-table-column prop="ranking_trade_date" label="排名日" width="115" />
        <el-table-column prop="stage_type" label="阶段" width="150" />
        <el-table-column prop="rank_ic" label="Quant Rank IC" width="130" />
        <el-table-column prop="flash_score_ic" label="Flash Score IC" width="130" />
        <el-table-column prop="incremental_lift" label="增量收益" width="110" />
        <el-table-column prop="promote_demote_spread" label="替换增量" width="110" />
        <el-table-column prop="calculation_status" label="成熟状态" min-width="150" />
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header><strong>每日明细</strong></template>
      <el-table :data="details" border>
        <el-table-column prop="stock_code" label="股票代码" width="100" />
        <el-table-column prop="stock_name" label="股票名称" width="115" />
        <el-table-column prop="quant_rank" label="Quant排名" width="95" />
        <el-table-column prop="quant_score" label="Quant分" width="90" />
        <el-table-column prop="flash_score" label="Flash分" width="90" />
        <el-table-column prop="flash_rank" label="Flash排名" width="95" />
        <el-table-column prop="selected_flag" label="选中" width="72" />
        <el-table-column prop="event_score" label="Event分" width="90" />
        <el-table-column prop="future_return" label="未来收益" width="105" />
        <el-table-column prop="outcome_status" label="收益状态" min-width="150" />
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header><strong>数据质量问题</strong></template>
      <el-table :data="issues" border>
        <el-table-column prop="affected_date" label="日期" width="115" />
        <el-table-column prop="issue_level" label="级别" width="105" />
        <el-table-column prop="issue_code" label="问题代码" width="260" />
        <el-table-column prop="affected_stock" label="股票" width="100" />
        <el-table-column prop="detail" label="说明" min-width="300" />
      </el-table>
    </el-card>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  modelEffectivenessApi,
  QUANT_FACTOR_VERSION,
  SCREENING_VERSIONS,
} from "@/api/modelEffectiveness";

const screeningVersions = SCREENING_VERSIONS;
const screeningVersion = ref<string>(SCREENING_VERSIONS[0]);
const horizon = ref(3);
const actionableOnly = ref(false);
const fullHorizon = ref(3);
const loading = ref(false);
const summary = ref<Record<string, any>>({});
const daily = ref<Record<string, any>[]>([]);
const details = ref<Record<string, any>[]>([]);
const issues = ref<Record<string, any>[]>([]);
const fullSummary = ref<Record<string, any>>({});
const fullIc = ref<Record<string, any>[]>([]);
const fullDeciles = ref<Record<string, any>[]>([]);
const fullFixed = ref<Record<string, any>[]>([]);
const fullHead = ref<Record<string, any>[]>([]);
const fullFactorIc = ref<Record<string, any>[]>([]);
const latest = computed(() => daily.value.at(-1));
const dataStatus = computed(() => String(summary.value?.data_status || "NOT_MATURED"));
const fullHeadline = (key: string) => {
  const value = (fullSummary.value?.headline_metrics as Record<string, any> | undefined)?.[
    `D${fullHorizon.value}`
  ]?.[key];
  return typeof value === "number" ? value.toFixed(4) : String(value ?? "—");
};
const fullPercent = (key: string) => {
  const value = (fullSummary.value?.headline_metrics as Record<string, any> | undefined)?.[
    `D${fullHorizon.value}`
  ]?.[key];
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
};

const params = () => ({
  quantFactorVersion: QUANT_FACTOR_VERSION,
  screeningVersion: screeningVersion.value,
  horizon: horizon.value,
  actionableOnly: actionableOnly.value,
});
const metric = (key: string) => {
  const value = latest.value?.[key];
  return typeof value === "number" ? value.toFixed(4) : "—";
};
const percent = (key: string) => {
  const value = latest.value?.[key];
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
};
async function load() {
  loading.value = true;
  try {
    const [summaryResult, dailyResult, detailResult, qualityResult] = await Promise.all([
      modelEffectivenessApi.summary(params()),
      modelEffectivenessApi.dailyMetrics(params()),
      modelEffectivenessApi.details(params()),
      modelEffectivenessApi.dataQuality(params()),
    ]);
    summary.value = summaryResult.data || {};
    daily.value = dailyResult.data || [];
    details.value = detailResult.data || [];
    issues.value = qualityResult.data || [];
  } finally {
    loading.value = false;
  }
}
async function loadFullUniverse() {
  const [summaryResult, icResult, decileResult, fixedResult, headResult, factorResult] =
    await Promise.all([
      modelEffectivenessApi.fullUniverseSummary(fullHorizon.value),
      modelEffectivenessApi.fullUniverseIc(fullHorizon.value),
      modelEffectivenessApi.fullUniverseDeciles(fullHorizon.value),
      modelEffectivenessApi.fullUniverseFixedBands(fullHorizon.value),
      modelEffectivenessApi.fullUniverseHeadBands(fullHorizon.value),
      modelEffectivenessApi.fullUniverseFactorIc(fullHorizon.value),
    ]);
  fullSummary.value = summaryResult.data || {};
  fullIc.value = icResult.data || [];
  fullDeciles.value = decileResult.data || [];
  fullFixed.value = fixedResult.data || [];
  fullHead.value = headResult.data || [];
  fullFactorIc.value = factorResult.data || [];
}
watch([screeningVersion, horizon, actionableOnly], load);
watch(fullHorizon, loadFullUniverse);
onMounted(() => {
  load();
  loadFullUniverse();
});
</script>

<style scoped>
.effectiveness-page { display: grid; gap: 16px; }
.heading { display: flex; justify-content: space-between; gap: 16px; }
h2 { margin: 0; color: #1b2b3d; }
p { margin: 6px 0 0; color: #667688; }
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-bottom: 14px; }
.cards { display: grid; grid-template-columns: repeat(6, minmax(130px, 1fr)); gap: 10px; margin-top: 14px; }
.cards div { padding: 12px; border: 1px solid #dfe7ef; border-radius: 6px; text-align: center; }
.cards span { display: block; color: #718096; font-size: 12px; }
.cards strong { display: block; margin-top: 7px; color: #17365d; }
@media (max-width: 1000px) { .cards { grid-template-columns: repeat(2, 1fr); } }
</style>
