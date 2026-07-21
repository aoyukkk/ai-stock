<template>
  <section class="market-review">
    <div class="page-head">
      <div><h1>每日大盘复盘</h1><p>{{ store.tradeDate }} 市场结构、公开证据与次日条件情景</p></div>
      <div class="actions">
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button v-if="auth.canWrite" type="primary" :loading="running" @click="runDataOnly">生成本地复盘</el-button>
        <el-button v-if="auth.canWrite" :disabled="running" @click="refreshEvidence">刷新公开证据</el-button>
        <el-button v-if="auth.canWrite" :disabled="!bundle.run?.run_id" @click="exportExcel">更新 Excel</el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <el-alert v-if="bundle.run?.search_status === 'UNAVAILABLE' || bundle.run?.search_status === 'DATA_ONLY'" title="当前为本地结构化数据复盘，公开证据未调用或不可用。" type="warning" show-icon :closable="false" />

    <div class="status-strip">
      <div><span>运行状态</span><StatusTag :status="bundle.run?.status || 'NOT_RUN'" /></div>
      <div><span>市场方向</span><strong>{{ directionLabel(bundle.run?.market_direction) }}</strong></div>
      <div><span>市场状态</span><strong>{{ regimeLabel(bundle.run?.market_regime) }}</strong></div>
      <div><span>证据状态</span><strong>{{ searchLabel(bundle.run?.search_status) }}</strong></div>
      <div><span>数据质量</span><strong>{{ number(bundle.snapshot?.data_quality_score) }}</strong></div>
      <div><span>置信度</span><strong>{{ percent(bundle.run?.confidence) }}</strong></div>
    </div>

    <div class="summary-band">
      <h2>{{ bundle.review?.headline || "尚未生成当日复盘" }}</h2>
      <p>{{ bundle.review?.market_summary || "点击“生成本地复盘”读取本地日行情缓存并执行规则计算。" }}</p>
      <div class="summary-grid">
        <span>{{ bundle.review?.breadth_summary || "市场宽度暂无" }}</span>
        <span>{{ bundle.review?.turnover_summary || "成交结构暂无" }}</span>
        <span>{{ bundle.review?.style_summary || "风格结构暂无" }}</span>
      </div>
    </div>

    <div class="metric-grid">
      <div><span>上涨家数</span><strong>{{ integer(breadth.advancing_count) }}</strong></div>
      <div><span>下跌家数</span><strong>{{ integer(breadth.declining_count) }}</strong></div>
      <div><span>上涨占比</span><strong>{{ percent(breadth.advancing_ratio) }}</strong></div>
      <div><span>等权涨跌幅</span><strong :class="tone(breadth.equal_weight_return)">{{ percent(breadth.equal_weight_return) }}</strong></div>
      <div><span>成交额</span><strong>{{ amountYi(turnover.total_amount) }}</strong></div>
      <div><span>涨停 / 跌停</span><strong>{{ integer(limits.limit_up_count) }} / {{ integer(limits.limit_down_count) }}</strong></div>
    </div>

    <el-tabs v-model="activeTab" class="review-tabs">
      <el-tab-pane label="主要指数" name="indices">
        <CenteredDataTable :rows="indices" :columns="indexColumns" :pagination-enabled="false" height="360" />
      </el-tab-pane>
      <el-tab-pane label="行业表现" name="industries">
        <CenteredDataTable :rows="industries" :columns="sectorColumns" :pagination-enabled="false" height="420" />
      </el-tab-pane>
      <el-tab-pane label="概念表现" name="concepts">
        <CenteredDataTable :rows="concepts" :columns="sectorColumns" :pagination-enabled="false" height="420" />
      </el-tab-pane>
      <el-tab-pane label="主要驱动" name="drivers">
        <CenteredDataTable :rows="drivers.items" :columns="driverColumns" :current-page="drivers.page" :page-size="drivers.page_size" :total="drivers.total" height="420" @pagination-change="changeDrivers" />
      </el-tab-pane>
      <el-tab-pane label="公开证据" name="evidence">
        <CenteredDataTable :rows="evidence.items" :columns="evidenceColumns" :current-page="evidence.page" :page-size="evidence.page_size" :total="evidence.total" height="420" @pagination-change="changeEvidence" @row-double-click="openEvidence" />
      </el-tab-pane>
    </el-tabs>

    <section class="outlook">
      <h2>次日条件情景</h2>
      <div class="scenario-grid">
        <article v-for="scenario in scenarios" :key="scenario.scenario_type">
          <header><strong>{{ scenarioLabel(scenario.scenario_type) }}</strong><span>{{ integer(scenario.probability) }}%</span></header>
          <h3>{{ scenario.title }}</h3><p>{{ scenario.description }}</p>
          <div><b>触发条件：</b>{{ join(scenario.triggers) }}</div>
          <div><b>失效条件：</b>{{ join(scenario.invalidation_conditions) }}</div>
        </article>
      </div>
    </section>

    <p class="disclaimer">本报告基于已取得的市场数据、公开信息与规则模型生成，仅用于市场复盘和模型验证，不构成投资建议。次日走势为条件情景分析，不是确定性预测。</p>

    <el-drawer v-model="drawer" title="公开证据详情" size="45%">
      <el-descriptions v-if="selectedEvidence" :column="1" border>
        <el-descriptions-item label="标题">{{ selectedEvidence.title }}</el-descriptions-item>
        <el-descriptions-item label="来源">{{ selectedEvidence.source_name }}</el-descriptions-item>
        <el-descriptions-item label="发布时间">{{ selectedEvidence.publish_time || "未知" }}</el-descriptions-item>
        <el-descriptions-item label="证据状态">{{ selectedEvidence.status }}</el-descriptions-item>
        <el-descriptions-item label="摘要">{{ selectedEvidence.summary }}</el-descriptions-item>
      </el-descriptions>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Refresh } from "@element-plus/icons-vue";

import { marketReviewApi } from "@/api/marketReview";
import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import StatusTag from "@/components/common/StatusTag.vue";
import { useWorkbenchStore } from "@/stores/workbench";
import { useInternalAuthStore } from "@/stores/internalAuth";
import type { MarketReviewBundle, MarketReviewPage } from "@/types/marketReview";
import type { TableColumn } from "@/types/workbench";

const store = useWorkbenchStore();
const auth = useInternalAuthStore();
const emptyPage = (): MarketReviewPage => ({ items: [], total: 0, page: 1, page_size: 20, total_pages: 0 });
const bundle = reactive<MarketReviewBundle>({ run: {}, snapshot: {}, review: {}, regime: {}, outlook: {}, search: {}, evidence: [], drivers: [], scenarios: [] });
const evidence = reactive<MarketReviewPage>(emptyPage());
const drivers = reactive<MarketReviewPage>(emptyPage());
const loading = ref(false); const running = ref(false); const error = ref(""); const activeTab = ref("indices");
const drawer = ref(false); const selectedEvidence = ref<Record<string, any> | null>(null);
const breadth = computed(() => bundle.snapshot?.breadth || {}); const turnover = computed(() => bundle.snapshot?.turnover || {}); const limits = computed(() => bundle.snapshot?.limit_structure || {});
const indices = computed(() => bundle.snapshot?.indices || []); const industries = computed(() => bundle.snapshot?.industries || []); const concepts = computed(() => bundle.snapshot?.concepts || []); const scenarios = computed(() => bundle.scenarios || []);
const indexColumns: TableColumn[] = [{ key: "index_name", label: "指数" }, { key: "index_code", label: "代码" }, { key: "close", label: "收盘" }, { key: "change_percent", label: "涨跌幅", formatter: percent }, { key: "trend_state", label: "趋势" }, { key: "data_status", label: "数据状态" }];
const sectorColumns: TableColumn[] = [{ key: "rank", label: "排名" }, { key: "sector_name", label: "板块", minWidth: 150 }, { key: "change_percent", label: "涨跌幅", formatter: percent }, { key: "advancing_ratio", label: "上涨占比", formatter: percent }, { key: "limit_up_count", label: "涨停数" }, { key: "member_count", label: "成分数" }];
const driverColumns: TableColumn[] = [{ key: "rank", label: "顺序" }, { key: "driver_type", label: "类型" }, { key: "direction", label: "方向" }, { key: "title", label: "驱动", minWidth: 190 }, { key: "impact_strength", label: "影响强度" }, { key: "confidence", label: "置信度", formatter: percent }, { key: "explanation", label: "说明", minWidth: 280 }];
const evidenceColumns: TableColumn[] = [{ key: "source_name", label: "来源" }, { key: "title", label: "标题", minWidth: 240 }, { key: "publish_time", label: "发布时间", minWidth: 170 }, { key: "source_tier", label: "来源等级" }, { key: "status", label: "状态" }, { key: "final_evidence_score", label: "证据评分", formatter: percent }];

async function load() { loading.value = true; error.value = ""; try { const [latest, driverPage, evidencePage] = await Promise.all([marketReviewApi.latest(store.tradeDate), marketReviewApi.drivers(store.tradeDate), marketReviewApi.evidence(store.tradeDate)]); Object.assign(bundle, latest.data || {}); Object.assign(drivers, driverPage.data); Object.assign(evidence, evidencePage.data); } catch (cause) { error.value = cause instanceof Error ? cause.message : "大盘复盘读取失败"; } finally { loading.value = false; } }
async function runDataOnly() { running.value = true; try { await marketReviewApi.run({ trade_date: store.tradeDate, mode: "DATA_ONLY" }); ElMessage.success("本地复盘任务已提交"); await store.refresh(); } finally { running.value = false; } }
async function refreshEvidence() { await ElMessageBox.confirm("此操作可能调用已配置的公开搜索服务，只保存标题、来源、时间和短摘要。是否继续？", "公开证据确认", { type: "warning" }); running.value = true; try { await marketReviewApi.refreshEvidence({ trade_date: store.tradeDate, mode: "REFRESH_EVIDENCE", force: true, allow_real_search: true }); ElMessage.success("证据刷新任务已提交"); await store.refresh(); } finally { running.value = false; } }
async function exportExcel() { await marketReviewApi.export({ trade_date: store.tradeDate, mode: "DATA_ONLY", force: true }); ElMessage.success("Excel 更新任务已提交"); }
async function changeDrivers({ page, pageSize }: { page: number; pageSize: number }) { Object.assign(drivers, (await marketReviewApi.drivers(store.tradeDate, page, pageSize)).data); }
async function changeEvidence({ page, pageSize }: { page: number; pageSize: number }) { Object.assign(evidence, (await marketReviewApi.evidence(store.tradeDate, page, pageSize)).data); }
function openEvidence(row: Record<string, any>) { selectedEvidence.value = row; drawer.value = true; }
function percent(value: unknown): string { return value === null || value === undefined ? "-" : `${(Number(value) * 100).toFixed(2)}%`; }
function number(value: unknown): string { return value === null || value === undefined ? "-" : Number(value).toFixed(1); }
function integer(value: unknown): string { return value === null || value === undefined ? "-" : Math.round(Number(value)).toLocaleString("zh-CN"); }
function amountYi(value: unknown): string { return value === null || value === undefined ? "-" : `${(Number(value) / 100_000_000).toFixed(2)}亿元`; }
function join(value: unknown): string { return Array.isArray(value) && value.length ? value.join("；") : "暂无"; }
function tone(value: unknown): string { return Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : ""; }
function directionLabel(value: unknown): string { return ({ UP: "整体上涨", DOWN: "整体下跌", FLAT: "窄幅震荡", MIXED: "结构分化" } as Record<string, string>)[String(value)] || "未生成"; }
function regimeLabel(value: unknown): string { return ({ STRONG_BULL: "强势上涨", BULL: "偏强", BALANCED: "均衡震荡", BEAR: "偏弱", STRONG_BEAR: "弱势下跌", HIGH_VOLATILITY: "高波动", LIQUIDITY_WEAK: "流动性偏弱", STRUCTURAL_DIVERGENCE: "结构分化" } as Record<string, string>)[String(value)] || String(value || "未生成"); }
function searchLabel(value: unknown): string { return ({ VERIFIED: "已核验", PARTIAL: "部分证据", UNAVAILABLE: "不可用", DATA_ONLY: "仅本地数据" } as Record<string, string>)[String(value)] || "未运行"; }
function scenarioLabel(value: unknown): string { return ({ BASE: "基准情景", BULL: "偏强情景", BEAR: "偏弱情景" } as Record<string, string>)[String(value)] || String(value); }
watch(() => store.tradeDate, () => void load()); onMounted(() => void load());
</script>

<style scoped>
.market-review { display: grid; gap: 14px; }.page-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; }.page-head h1 { margin: 0; font-size: 22px; }.page-head p { margin: 5px 0 0; color: #667085; }.actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }.status-strip { display: grid; grid-template-columns: repeat(6,minmax(120px,1fr)); background: #fff; border: 1px solid #dfe5ec; }.status-strip>div,.metric-grid>div { display: grid; place-items: center; gap: 6px; min-height: 74px; padding: 10px; border-right: 1px solid #e6ebf0; text-align: center; }.status-strip span,.metric-grid span { color: #667085; font-size: 12px; }.summary-band { padding: 18px 20px; background: #fff; border: 1px solid #dfe5ec; }.summary-band h2 { margin: 0 0 8px; font-size: 18px; }.summary-band p { color: #475467; line-height: 1.7; }.summary-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; color: #344054; text-align: center; }.metric-grid { display: grid; grid-template-columns: repeat(6,1fr); background: #fff; border: 1px solid #dfe5ec; }.metric-grid strong { font-size: 20px; }.positive { color: #c0392b; }.negative { color: #16834b; }.review-tabs,.outlook { padding: 12px 16px; background: #fff; border: 1px solid #dfe5ec; }.outlook h2 { margin: 4px 0 12px; font-size: 18px; }.scenario-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; }.scenario-grid article { padding: 14px; border: 1px solid #dfe5ec; border-radius: 6px; }.scenario-grid header { display: flex; justify-content: space-between; }.scenario-grid h3 { font-size: 15px; }.scenario-grid p,.scenario-grid div { color: #475467; font-size: 13px; line-height: 1.65; }.disclaimer { margin: 0; color: #667085; font-size: 12px; text-align: center; }.el-alert { margin: 0; }@media(max-width:1100px){.status-strip,.metric-grid{grid-template-columns:repeat(3,1fr)}.summary-grid,.scenario-grid{grid-template-columns:1fr}.page-head{align-items:flex-start;flex-direction:column}.actions{justify-content:flex-start}}
</style>
