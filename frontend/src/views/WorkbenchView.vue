<template>
  <section>
    <div class="page-title">
      <div><h1>流程工作台</h1><p>按交易日读取数据库中已完成的正式流水线结果。</p></div>
      <el-button :loading="store.loading" @click="store.refresh">刷新状态</el-button>
    </div>

    <el-alert v-if="store.status?.source_mode === 'EMPTY'" title="该交易日没有已完成的正式流水线结果。" type="info" show-icon :closable="false" />
    <el-alert v-else-if="store.status?.consistency?.status === 'WARNING'" title="历史结果存在数量差异，请查看一致性校验。" type="warning" show-icon :closable="false" />
    <el-alert v-if="store.error" :title="store.error" type="error" show-icon :closable="false" />

    <JobProgressPanel
      class="job-progress"
      :job="store.currentJob || undefined"
      @cancel="cancelJob"
      @resume="resumeJob"
    />

    <el-card v-if="store.status?.source_mode === 'DATABASE'" shadow="never" class="run-summary">
      <span>数据来源：数据库历史结果</span>
      <span>流水线：{{ shortId(store.status.pipeline_run_id) }}</span>
      <span>状态：<StatusTag :status="store.status.pipeline_status" /></span>
    </el-card>

    <div class="run-profile">
      <span>量化范围：{{ store.settings.quant_top_n ?? "-" }}</span>
      <span>Flash 分析：{{ store.settings.llm_analysis_n ?? "-" }}</span>
      <span>最终入选：{{ store.settings.llm_top_n ?? "-" }}</span>
      <span>每日 Token 上限：{{ store.settings.daily_token_limit ?? "-" }}</span>
      <span>Flash 已用：{{ store.status?.flash_budget?.used ?? 0 }} / {{ store.status?.flash_budget?.limit ?? "-" }}</span>
    </div>

    <el-card shadow="never" class="steps">
      <el-steps :active="activeStep" finish-status="success" align-center>
        <el-step v-for="step in steps" :key="step.key" :title="step.title" :description="statusLabel(stepStatus(step))" />
      </el-steps>
    </el-card>

    <div class="action-grid">
      <el-card v-for="step in steps" :key="step.key" shadow="never">
        <template #header><div class="card-head"><span>{{ step.title }}</span><StatusTag :status="stepStatus(step)" /></div></template>
        <div class="metrics">
          <span>数量：{{ stepCount(step) }}</span>
          <span>Run：{{ shortId(stepRunId(step)) }}</span>
          <span>来源：{{ store.status?.source_mode === 'DATABASE' ? '数据库历史结果' : '无数据' }}</span>
          <template v-if="step.key === 'flash'"><span>成功：{{ store.status?.counts.flash_success ?? 0 }}</span><span>失败：{{ store.status?.counts.flash_failure ?? 0 }}</span><span>Top：{{ store.status?.counts.llm_top ?? 0 }}</span></template>
          <template v-if="step.key === 'manual'"><span>与 LLM 重合：{{ store.status?.counts.both ?? 0 }}</span><span>仅人工：{{ store.status?.counts.manual_only ?? 0 }}</span></template>
          <template v-if="step.key === 'final'"><span>挂单：{{ store.status?.counts.order ?? 0 }}</span><span>仓位：{{ store.status?.counts.position ?? 0 }}</span><span>非零仓位：{{ store.status?.counts.non_zero_position ?? 0 }}</span></template>
        </div>
        <div class="card-actions">
          <el-button
            v-if="step.key !== 'manual'"
            type="primary"
            plain
            :disabled="store.hasActiveJob"
            :loading="runningStep === step.key || activeJobType === step.key.toUpperCase()"
            @click="execute(step)"
          >执行任务</el-button>
          <el-button v-else type="primary" plain @click="$router.push(step.path)">管理人工池</el-button>
          <el-button text @click="$router.push(step.path)">查看结果</el-button>
        </div>
      </el-card>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import StatusTag from "@/components/common/StatusTag.vue";
import JobProgressPanel from "@/components/common/JobProgressPanel.vue";
import { useWorkbenchStore } from "@/stores/workbench";
import type { RunStatus } from "@/types/workbench";

type Step = { key: "data" | "quant" | "flash" | "manual" | "final" | "export"; title: string; path: string };
const store = useWorkbenchStore();
const runningStep = ref<string | null>(null);
const activeJobType = computed(() => store.hasActiveJob ? store.currentJob?.job_type : null);
const steps: Step[] = [
  { key: "data", title: "获取数据", path: "/data-status" },
  { key: "quant", title: "量化排序", path: "/quant-ranking" },
  { key: "flash", title: "LLM 二筛", path: "/llm-screening" },
  { key: "manual", title: "人工选股", path: "/manual-selection" },
  { key: "final", title: "生成终排", path: "/final-ranking" },
  { key: "export", title: "导出 Excel", path: "/order-position" }
];
const complete = new Set(["READY", "LOADED", "COMPLETED", "SUCCESS", "PARTIAL_SUCCESS"]);
const activeStep = computed(() => steps.filter((step) => complete.has(stepStatus(step))).length);

function stepStatus(step: Step): RunStatus { return store.status?.[step.key]?.status ?? "EMPTY"; }
function stepCount(step: Step): number { return store.status?.[step.key]?.count ?? 0; }
function stepRunId(step: Step): string | null { return store.status?.[step.key]?.run_id ?? null; }
function shortId(value?: string | null): string { return value ? `${value.slice(0, 18)}${value.length > 18 ? "…" : ""}` : "-"; }
function statusLabel(value: string): string { return ({ READY: "就绪", LOADED: "已加载", COMPLETED: "已完成", SUCCESS: "成功", PARTIAL_SUCCESS: "部分成功", EMPTY: "无结果" } as Record<string, string>)[value] || value; }
async function execute(step: Step) {
  if (step.key === "manual") return;
  const type = step.key;
  const llmTask = type === "flash" || type === "final";
  if (llmTask) {
    await ElMessageBox.confirm(
      `该任务会调用真实模型。当前流程为 ${store.settings.quant_top_n ?? "-"} → ${store.settings.llm_analysis_n ?? "-"} → ${store.settings.llm_top_n ?? "-"}，每日 Token 上限 ${store.settings.daily_token_limit ?? "-"}。Flash 当日已用 ${store.status?.flash_budget?.used ?? 0} / ${store.status?.flash_budget?.limit ?? "-"}。是否继续？`,
      "预算确认",
      { confirmButtonText: "确认执行", cancelButtonText: "取消", type: "warning" }
    );
  }
  runningStep.value = step.key;
  try {
    const job = await store.run(type, llmTask);
    ElMessage.success(job.status === "SUCCESS" ? "已加载完成结果" : "任务已进入后台队列");
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "任务启动失败，请检查数据、密钥和时间一致性状态");
  } finally {
    runningStep.value = null;
  }
}

async function resumeJob(jobId: string) {
  try {
    await store.resume(jobId);
    ElMessage.success("已从检查点继续运行");
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "断点续跑失败");
  }
}

async function cancelJob(jobId: string) {
  try {
    await store.cancel(jobId);
    ElMessage.success("已提交取消请求");
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "取消任务失败");
  }
}
</script>

<style scoped>
.page-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.page-title h1 { margin: 0; font-size: 22px; }
.page-title p { margin: 6px 0 0; color: #667085; }
.run-summary { margin: 12px 0; }
.job-progress { margin: 12px 0; }
.run-profile { display: flex; justify-content: center; gap: 24px; flex-wrap: wrap; margin: 12px 0; color: #475467; font-size: 13px; }
.run-summary :deep(.el-card__body) { display: flex; justify-content: center; align-items: center; gap: 28px; flex-wrap: wrap; text-align: center; }
.steps { margin: 12px 0; }
.action-grid { display: grid; grid-template-columns: repeat(3, minmax(240px, 1fr)); gap: 10px; }
.card-head, .card-actions { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.metrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; min-height: 72px; margin-bottom: 12px; color: #596579; font-size: 13px; text-align: center; }
@media (max-width: 900px) { .action-grid { grid-template-columns: 1fr; } }
</style>
