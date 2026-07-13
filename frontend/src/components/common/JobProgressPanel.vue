<template>
  <el-card shadow="never" class="job-panel">
    <template #header><div class="head"><span>当前任务进度</span><StatusTag :status="job?.status" /></div></template>
    <div v-if="job" class="job-grid">
      <span>任务阶段</span><strong>{{ job.stage }}</strong>
      <span>进度</span><strong>{{ job.progress_current }} / {{ job.progress_total }}（{{ percent }}%）</strong>
      <span>成功 / 失败</span><strong>{{ job.success_count }} / {{ job.failure_count }}</strong>
      <span>当前股票</span><strong>{{ job.current_stock || "-" }}</strong>
      <span>Token / 成本</span><strong>{{ job.token_usage }} / {{ job.cost_usd }}</strong>
      <span>开始 / 结束</span><strong>{{ job.started_at || "-" }} / {{ job.finished_at || "-" }}</strong>
    </div>
    <el-empty v-else description="当前没有运行任务" :image-size="54" />
  </el-card>
</template>

<script setup lang="ts">
import { computed } from "vue";
import StatusTag from "@/components/common/StatusTag.vue";
import type { PipelineJob } from "@/types/workbench";

const props = defineProps<{ job?: PipelineJob }>();
const percent = computed(() => props.job?.progress_total ? Math.round((props.job.progress_current / props.job.progress_total) * 100) : 0);
</script>

<style scoped>
.head { display: flex; align-items: center; justify-content: space-between; }
.job-grid { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 10px 16px; align-items: center; }
.job-grid span { color: #667085; text-align: center; }
.job-grid strong { text-align: center; word-break: break-word; }
</style>
