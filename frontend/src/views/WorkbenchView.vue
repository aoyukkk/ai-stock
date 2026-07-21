<template>
  <section class="workbench-page">
    <header class="page-head">
      <div><span class="eyebrow">{{ store.tradeDate }}</span><h1>每日流程工作台</h1><p>{{ sourceSummary }}</p></div>
      <el-button :icon="Refresh" :loading="store.loading" @click="store.refresh">刷新状态</el-button>
    </header>

    <div class="quick-actions" aria-label="日常高频入口">
      <button v-for="item in quickActions" :key="item.path" type="button" @click="$router.push(item.path)">
        <span class="quick-icon"><el-icon><component :is="item.icon" /></el-icon></span>
        <span><strong>{{ item.title }}</strong><small>{{ item.meta }}</small></span>
        <el-icon class="quick-arrow"><ArrowRight /></el-icon>
      </button>
    </div>

    <el-alert v-if="store.status?.source_mode === 'EMPTY'" title="该交易日没有已完成的正式流水线结果。" type="info" show-icon :closable="false" />
    <el-alert v-else-if="store.status?.consistency?.status === 'WARNING'" title="历史结果存在数量差异，请查看一致性校验。" type="warning" show-icon :closable="false" />
    <el-alert v-if="store.error" :title="store.error" type="error" show-icon :closable="false" />

    <JobProgressPanel class="job-progress" :job="store.currentJob || undefined" :can-operate="auth.canWrite" @cancel="cancelJob" @resume="resumeJob" />

    <section class="overview-band" aria-label="今日运行概览">
      <div><span>数据来源</span><strong>{{ sourceLabel }}</strong></div>
      <div><span>量化范围</span><strong>{{ number(store.settings.quant_top_n) }}</strong></div>
      <div><span>Flash 分析</span><strong>{{ number(store.settings.llm_analysis_n) }}</strong></div>
      <div><span>最终入选</span><strong>{{ number(store.settings.llm_top_n) }}</strong></div>
      <div><span>Token 使用</span><strong>{{ number(store.status?.token.used) }} / {{ number(store.status?.token.limit) }}</strong></div>
      <div><span>流水线</span><strong class="mono">{{ shortId(store.status?.pipeline_run_id) }}</strong></div>
    </section>

    <section class="pipeline-panel">
      <div class="panel-head">
        <div><h2>正式流水线</h2><p>{{ completedCount }} / {{ steps.length }} 个阶段已有可用结果</p></div>
        <StatusTag :status="store.status?.pipeline_status" />
      </div>
      <div class="pipeline-header" aria-hidden="true"><span>阶段</span><span>状态</span><span>结果数</span><span>Run</span><span>操作</span></div>
      <div v-for="(step, index) in steps" :key="step.key" class="pipeline-row">
        <div class="stage-name"><span class="stage-index">{{ index + 1 }}</span><div><strong>{{ step.title }}</strong><small>{{ step.subtitle }}</small></div></div>
        <div><StatusTag :status="stepStatus(step)" /></div>
        <strong class="result-count">{{ stepCount(step).toLocaleString("zh-CN") }}</strong>
        <span class="run-id mono" :title="stepRunId(step) || ''">{{ shortId(stepRunId(step)) }}</span>
        <div class="row-actions">
          <el-button v-if="auth.canWrite && step.key !== 'manual'" type="primary" plain size="small" :disabled="store.hasActiveJob" :loading="runningStep === step.key || activeJobType === step.key.toUpperCase()" @click="execute(step)">执行</el-button>
          <el-button v-else-if="auth.canWrite" type="primary" plain size="small" @click="$router.push(step.path)">管理</el-button>
          <el-button size="small" @click="$router.push(step.path)">查看</el-button>
        </div>
      </div>
    </section>

    <footer class="safety-note"><span />辅助决策模式 · 真实交易关闭 · 所有订单均需人工确认</footer>
  </section>
</template>

<script setup lang="ts">
import { ArrowRight, Bell, Monitor, Refresh, TrendCharts, Wallet } from "@element-plus/icons-vue";
import { computed, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import JobProgressPanel from "@/components/common/JobProgressPanel.vue";
import StatusTag from "@/components/common/StatusTag.vue";
import { useWorkbenchStore } from "@/stores/workbench";
import { useInternalAuthStore } from "@/stores/internalAuth";
import type { RunStatus } from "@/types/workbench";

type Step = { key: "data" | "quant" | "flash" | "manual" | "final" | "market_review" | "export"; title: string; subtitle: string; path: string };
const store = useWorkbenchStore();
const auth = useInternalAuthStore();
const runningStep = ref<string | null>(null);
const activeJobType = computed(() => store.hasActiveJob ? store.currentJob?.job_type : null);
const allQuickActions = [
  { title: "午间推荐", meta: "11:30 快速分析", path: "/midday-recommendation", icon: Monitor },
  { title: "实时盯盘", meta: "选中股票提醒", path: "/realtime-monitor", icon: Bell },
  { title: "盘后建议", meta: "持仓与候选复核", path: "/post-close-actions", icon: Wallet },
  { title: "大盘复盘", meta: "指数与行业轮动", path: "/market-review", icon: TrendCharts },
];
const quickActions = computed(() => allQuickActions.filter((item) => auth.canWrite || item.path === "/market-review"));
const steps: Step[] = [
  { key: "data", title: "获取数据", subtitle: "日频行情与基础数据", path: "/data-status" },
  { key: "quant", title: "量化排序", subtitle: "全 A 确定性因子排名", path: "/quant-ranking" },
  { key: "flash", title: "LLM 二筛", subtitle: "候选池快速复核", path: "/llm-screening" },
  { key: "manual", title: "人工选股", subtitle: "交易员补充候选", path: "/manual-selection" },
  { key: "final", title: "生成终排", subtitle: "Pro 复核与最终排序", path: "/final-ranking" },
  { key: "market_review", title: "大盘复盘", subtitle: "市场结构与行业轮动", path: "/market-review" },
  { key: "export", title: "导出结果", subtitle: "价格、仓位与人类阅读表格", path: "/order-position" },
];
const complete = new Set(["READY", "LOADED", "COMPLETED", "SUCCESS", "PARTIAL_SUCCESS"]);
const completedCount = computed(() => steps.filter((step) => complete.has(stepStatus(step))).length);
const sourceLabel = computed(() => store.status?.source_mode === "DATABASE" ? "数据库结果" : store.status?.source_mode === "MOCK" ? "演示数据" : "暂无数据");
const sourceSummary = computed(() => store.status?.source_mode === "DATABASE" ? "当前展示已持久化的正式运行结果" : "选择交易日后查看或启动当日流程");

function stepStatus(step: Step): RunStatus { return store.status?.[step.key]?.status ?? "EMPTY"; }
function stepCount(step: Step): number { return store.status?.[step.key]?.count ?? 0; }
function stepRunId(step: Step): string | null { return store.status?.[step.key]?.run_id ?? null; }
function shortId(value?: string | null): string { return value ? `${value.slice(0, 12)}${value.length > 12 ? "…" : ""}` : "-"; }
function number(value?: unknown): string { const parsed = Number(value ?? 0); return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN") : "-"; }
async function execute(step: Step) {
  if (step.key === "manual") return;
  const type = step.key;
  const llmTask = type === "flash" || type === "final";
  if (llmTask) {
    await ElMessageBox.confirm(
      `当前流程为 ${store.settings.quant_top_n ?? "-"} → ${store.settings.llm_analysis_n ?? "-"} → ${store.settings.llm_top_n ?? "-"}，每日 Token 上限 ${store.settings.daily_token_limit ?? "-"}。确认执行？`,
      "预算确认", { confirmButtonText: "确认执行", cancelButtonText: "取消", type: "warning" }
    );
  }
  runningStep.value = step.key;
  try {
    const job = await store.run(type, llmTask);
    ElMessage.success(job.status === "SUCCESS" ? "结果已加载" : "任务已进入后台队列");
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "任务启动失败");
  } finally { runningStep.value = null; }
}
async function resumeJob(jobId: string) { try { await store.resume(jobId); ElMessage.success("已从检查点继续运行"); } catch (cause) { ElMessage.error(cause instanceof Error ? cause.message : "断点续跑失败"); } }
async function cancelJob(jobId: string) { try { await store.cancel(jobId); ElMessage.success("已提交取消请求"); } catch (cause) { ElMessage.error(cause instanceof Error ? cause.message : "取消任务失败"); } }
</script>

<style scoped>
.workbench-page { display: grid; gap: 14px; max-width: 1480px; margin: 0 auto; }
.page-head, .panel-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.eyebrow { color: #47739e; font-size: 12px; font-weight: 700; }
.page-head h1 { margin: 3px 0 4px; color: #17283b; font-size: 24px; }
.page-head p, .panel-head p { margin: 0; color: #6f7d8d; font-size: 13px; }
.quick-actions { display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); border: 1px solid #dce4eb; background: #fff; }
.quick-actions button { min-height: 76px; display: flex; align-items: center; gap: 11px; padding: 12px 14px; border: 0; border-right: 1px solid #e3e9ee; background: transparent; color: #243448; text-align: left; cursor: pointer; }
.quick-actions button:last-child { border-right: 0; }
.quick-actions button:hover { background: #f3f7fb; }
.quick-icon { width: 36px; height: 36px; display: grid; place-items: center; flex: 0 0 36px; border-radius: 6px; background: #e9f1f9; color: #235b91; font-size: 18px; }
.quick-actions strong, .quick-actions small { display: block; }
.quick-actions strong { font-size: 14px; }
.quick-actions small { margin-top: 4px; color: #7a8795; font-size: 11px; }
.quick-arrow { margin-left: auto; color: #91a0ae; }
.job-progress { margin: 0; }
.overview-band { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); border: 1px solid #dce4eb; background: #fff; }
.overview-band div { min-height: 70px; display: grid; place-items: center; align-content: center; gap: 6px; padding: 10px; border-right: 1px solid #e5eaef; text-align: center; }
.overview-band div:last-child { border-right: 0; }
.overview-band span { color: #7c8996; font-size: 11px; }
.overview-band strong { color: #1e3044; font-size: 15px; overflow-wrap: anywhere; }
.pipeline-panel { border: 1px solid #dce4eb; background: #fff; }
.panel-head { min-height: 70px; padding: 13px 16px; border-bottom: 1px solid #e4e9ee; }
.panel-head h2 { margin: 0 0 4px; color: #203246; font-size: 17px; }
.pipeline-header, .pipeline-row { display: grid; grid-template-columns: minmax(240px, 1.6fr) 120px 100px minmax(140px, .8fr) 150px; align-items: center; gap: 10px; }
.pipeline-header { min-height: 34px; padding: 0 16px; background: #f6f8fa; color: #7b8895; font-size: 11px; text-align: center; }
.pipeline-header span:first-child { text-align: left; }
.pipeline-row { min-height: 68px; padding: 8px 16px; border-top: 1px solid #edf0f3; text-align: center; }
.pipeline-row:hover { background: #fafcfd; }
.stage-name { display: flex; align-items: center; gap: 11px; text-align: left; }
.stage-index { width: 28px; height: 28px; display: grid; place-items: center; flex: 0 0 28px; border: 1px solid #cdd8e2; border-radius: 50%; color: #46647f; font-size: 12px; font-weight: 700; }
.stage-name strong, .stage-name small { display: block; }
.stage-name strong { color: #27394d; font-size: 14px; }
.stage-name small { margin-top: 3px; color: #84909d; font-size: 11px; }
.result-count { color: #25384c; font-size: 15px; }
.run-id { color: #687787; font-size: 11px; overflow-wrap: anywhere; }
.row-actions { display: flex; justify-content: center; gap: 6px; }
.mono { font-family: Consolas, "SFMono-Regular", monospace; }
.safety-note { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 5px; color: #668072; font-size: 11px; }
.safety-note span { width: 6px; height: 6px; border-radius: 50%; background: #3ca36a; }
@media (max-width: 1050px) { .quick-actions { grid-template-columns: repeat(2, 1fr); } .quick-actions button:nth-child(2) { border-right: 0; } .quick-actions button:nth-child(-n+2) { border-bottom: 1px solid #e3e9ee; } .overview-band { grid-template-columns: repeat(3, 1fr); } .overview-band div:nth-child(3) { border-right: 0; } .overview-band div:nth-child(-n+3) { border-bottom: 1px solid #e5eaef; } }
@media (max-width: 760px) {
  .page-head { align-items: flex-start; }
  .page-head h1 { font-size: 20px; }
  .page-head p { max-width: 220px; }
  .quick-actions { grid-template-columns: 1fr 1fr; }
  .quick-actions button { min-width: 0; padding: 10px; }
  .quick-icon { display: none; }
  .quick-arrow { display: none; }
  .overview-band { grid-template-columns: repeat(2, 1fr); }
  .overview-band div:nth-child(odd) { border-right: 1px solid #e5eaef; }
  .overview-band div:nth-child(even) { border-right: 0; }
  .overview-band div { border-bottom: 1px solid #e5eaef; }
  .overview-band div:nth-last-child(-n+2) { border-bottom: 0; }
  .pipeline-header { display: none; }
  .pipeline-row { grid-template-columns: minmax(0, 1fr) auto; gap: 8px; padding: 12px; }
  .pipeline-row > :nth-child(2) { grid-column: 2; grid-row: 1; }
  .pipeline-row > :nth-child(3) { display: none; }
  .pipeline-row > :nth-child(4) { grid-column: 1 / -1; text-align: left; padding-left: 39px; }
  .pipeline-row > :nth-child(5) { grid-column: 1 / -1; justify-content: flex-end; }
}
</style>
