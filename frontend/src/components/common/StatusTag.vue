<template><el-tag :type="type" effect="light" size="small" :title="props.status || 'EMPTY'">{{ label }}</el-tag></template>
<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ status?: string | null }>();
const labels: Record<string, string> = {
  READY: "就绪", LOADED: "已加载", COMPLETED: "已完成", SUCCESS: "成功",
  PARTIAL_SUCCESS: "部分成功", NOT_RUN: "未运行", RUNNING: "运行中",
  PENDING: "等待中", FAILED: "失败", BLOCKED: "已阻断", CANCELLED: "已取消",
  EMPTY: "无结果", WARNING: "有差异", NOT_READY: "未就绪"
};
const label = computed(() => labels[props.status || "EMPTY"] || "未知");
const type = computed(() => (["SUCCESS", "READY", "LOADED", "COMPLETED"].includes(props.status || "") ? "success" : ["PARTIAL_SUCCESS", "WARNING"].includes(props.status || "") ? "warning" : ["FAILED", "BLOCKED"].includes(props.status || "") ? "danger" : ["RUNNING", "PENDING"].includes(props.status || "") ? "primary" : "info"));
</script>
