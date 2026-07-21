<template>
  <section>
    <div class="page-title"><div><h1>人工选股</h1><p>历史 Run 快照只读；当前人工池可创建新版本，不覆盖历史结果。</p></div></div>
    <el-tabs v-model="activeTab">
      <el-tab-pane label="历史 Run 快照" name="snapshot">
        <CenteredDataTable :rows="snapshotRows" :columns="snapshotColumns" :loading="loading" />
      </el-tab-pane>
      <el-tab-pane label="当前可编辑人工池" name="current">
        <el-card shadow="never" class="form-card">
          <el-form label-position="top" class="selection-form">
            <el-form-item label="股票代码"><el-input v-model="stockCode" placeholder="例如 600519 或 600519.SH" /></el-form-item>
            <el-form-item label="优先级"><el-select v-model="priority"><el-option label="高" value="HIGH" /><el-option label="中" value="MEDIUM" /><el-option label="低" value="LOW" /></el-select></el-form-item>
            <el-form-item label="人工原因"><el-input v-model="reason" placeholder="记录加入当前候选池的依据" /></el-form-item>
            <el-form-item class="form-action"><el-button type="primary" @click="add">添加</el-button></el-form-item>
          </el-form>
        </el-card>
        <div class="table-actions"><el-button type="primary" :disabled="!monitorRows.length" @click="addManualToMonitor">加入盯盘</el-button></div>
        <CenteredDataTable :rows="currentRows" :columns="currentColumns" selectable @selection-change="handleMonitorSelection">
          <template #actions="{ row }"><el-button text type="danger" @click="remove(Number(row.id))">删除</el-button></template>
        </CenteredDataTable>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { ElMessage } from "element-plus";

import { workbenchApi } from "@/api/workbench";
import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import { useMonitorPool } from "@/composables/useMonitorPool";
import { useWorkbenchStore } from "@/stores/workbench";
import type { MonitorPoolCandidate } from "@/types/realtime";

const store = useWorkbenchStore();
const activeTab = ref("snapshot");
const snapshotRows = ref<Record<string, unknown>[]>([]);
const currentRows = ref<Record<string, unknown>[]>([]);
const stockCode = ref("");
const reason = ref("");
const priority = ref("MEDIUM");
const loading = ref(false);
const monitorRows = ref<Record<string, unknown>[]>([]);
const { addToMonitor } = useMonitorPool();
let controller: AbortController | null = null;
const snapshotColumns = [
  { key: "stock_code", label: "股票代码" }, { key: "stock_name", label: "股票名称" },
  { key: "source", label: "历史来源" }, { key: "read_only", label: "只读快照" },
  { key: "flash_run_id", label: "Flash Run", minWidth: 220 }, { key: "pro_run_id", label: "Pro Run", minWidth: 220 }
];
const currentColumns = [
  { key: "stock_code", label: "股票代码" }, { key: "priority", label: "优先级" },
  { key: "reason", label: "人工原因", minWidth: 300 }, { key: "created_at", label: "添加时间", minWidth: 180 },
  { key: "actions", label: "操作" }
];

async function load() {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  const selectedDate = store.tradeDate;
  try {
    const [snapshot, current] = await Promise.all([
      workbenchApi.manualSnapshot(selectedDate, store.status?.pipeline_run_id, controller.signal),
      workbenchApi.manual(selectedDate)
    ]);
    if (selectedDate !== store.tradeDate) return;
    snapshotRows.value = snapshot.data.items;
    currentRows.value = current.data.items.map((item) => ({ ...item }));
  } finally { if (!controller.signal.aborted) loading.value = false; }
}
async function add() {
  if (!stockCode.value.trim()) return;
  await workbenchApi.addManual({ trade_date: store.tradeDate, stock_code: stockCode.value, reason: reason.value, priority: priority.value, quant_run_id: store.status?.latest_run_ids.quant_run_id });
  stockCode.value = "";
  await load();
  ElMessage.success("已添加到当前人工池；历史快照未修改");
}
async function remove(id: number) { await workbenchApi.deleteManual(id); await load(); }
function handleMonitorSelection(value: Record<string, unknown>[]) { monitorRows.value = value; }
async function addManualToMonitor() {
  const candidates: MonitorPoolCandidate[] = monitorRows.value.map((row) => ({
    stock_code: String(row.stock_code || ""), stock_name: String(row.stock_name || ""),
    sources: ["MANUAL_SELECTION"], monitor_profile: "CANDIDATE_MONITOR",
    priority: String(row.priority || "HIGH") === "LOW" ? "LOW" : "HIGH"
  }));
  await addToMonitor(store.tradeDate, candidates, "MANUAL_SELECTION", store.status?.pipeline_run_id || undefined);
}
watch(() => [store.tradeDate, store.status?.pipeline_run_id], () => void load(), { immediate: true });
onBeforeUnmount(() => controller?.abort());
</script>

<style scoped>
.page-title { margin-bottom: 12px; }
.page-title h1 { margin: 0; font-size: 22px; }
.page-title p { margin: 5px 0 0; color: #667085; font-size: 13px; }
.form-card { margin-bottom: 12px; }
.selection-form { display: grid; grid-template-columns: 1.2fr 140px 2fr auto; gap: 10px; align-items: end; }
.form-action { margin-bottom: 18px; }
.table-actions { display: flex; justify-content: flex-end; margin: 0 0 10px; }
@media (max-width: 900px) { .selection-form { grid-template-columns: 1fr; } }
</style>
