<template>
  <el-card shadow="never" class="job-panel">
    <template #header>
      <div class="head"><span>当前任务进度</span><StatusTag :status="job?.status" /></div>
    </template>
    <div v-if="job" class="job-grid">
      <span>任务阶段</span><strong>{{ stageLabel }}</strong>
      <span>进度</span><strong>{{ job.progress_current }} / {{ job.progress_total }}（{{ percent }}%）</strong>
      <span>成功 / 失败</span><strong>{{ job.success_count }} / {{ job.failure_count }}</strong>
      <span>当前股票</span><strong>{{ job.current_stock || "-" }}</strong>
      <span>累计 Token</span><strong>{{ job.token_usage || 0 }}</strong>
      <span>开始 / 结束</span><strong>{{ job.started_at || "-" }} / {{ job.finished_at || "-" }}</strong>
    </div>
    <el-alert
      v-if="job?.error_code || job?.error_message"
      class="job-error"
      type="error"
      :title="job.error_code || '任务失败'"
      :description="job.error_message || ''"
      show-icon
      :closable="false"
    />
    <div v-if="job" class="actions">
      <el-button v-if="canOperate && active" type="danger" plain @click="$emit('cancel', job.job_id)">取消任务</el-button>
      <el-button v-if="canOperate && resumable" type="primary" @click="$emit('resume', job.job_id)">断点续跑</el-button>
    </div>
    <el-empty v-else description="当前没有任务记录" :image-size="54" />
  </el-card>
</template>

<script setup lang="ts">
import { computed } from "vue";
import StatusTag from "@/components/common/StatusTag.vue";
import { isActiveJob, isResumableJob } from "@/stores/workbench";
import type { PipelineJob } from "@/types/workbench";

const props = withDefaults(defineProps<{ job?: PipelineJob; canOperate?: boolean }>(), { canOperate: true });
defineEmits<{ cancel: [jobId: string]; resume: [jobId: string] }>();
const percent = computed(() => {
  if (!props.job?.progress_total) return 0;
  return Math.min(100, Math.round((props.job.progress_current / props.job.progress_total) * 100));
});
const active = computed(() => isActiveJob(props.job));
const resumable = computed(() => isResumableJob(props.job));
const stageLabel = computed(() => ({
  QUEUED: "等待执行", STARTING: "正在启动", TUSHARE_BATCH_UPDATE: "更新行情数据",
  QUANT_NO_LLM: "全市场量化（不调用模型）", FUNDAMENTAL_V4_FLASH_V5: "基本面与 Flash 二筛",
  PRO_V3_LOCAL_RANKING: "Pro 复核与最终排序", OPENPYXL_EXCEL_EXPORT: "生成 Excel",
  CANCELLATION_REQUESTED: "正在取消", COMPLETED: "已完成", FAILED: "失败"
} as Record<string, string>)[props.job?.stage || ""] || props.job?.stage || "-");
</script>

<style scoped>
.head, .actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.job-grid { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 10px 16px; align-items: center; }
.job-grid span { color: #667085; text-align: center; }
.job-grid strong { text-align: center; word-break: break-word; }
.job-error, .actions { margin-top: 14px; }
.actions { justify-content: flex-end; }
</style>
