<template>
  <section>
    <div class="page-title"><h1>运行记录</h1><el-button :loading="loading" @click="load">刷新</el-button></div>
    <CenteredDataTable
      :rows="rows"
      :columns="columns"
      :loading="loading"
      :total="total"
      :current-page="query.page"
      :page-size="query.pageSize"
      height="calc(100vh - 230px)"
      @pagination-change="changePage"
    />
  </section>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from "vue";

import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import { workbenchApi } from "@/api/workbench";
import { useWorkbenchStore } from "@/stores/workbench";

const store = useWorkbenchStore();
const rows = ref<Record<string, unknown>[]>([]);
const total = ref(0);
const loading = ref(false);
const query = reactive({ page: 1, pageSize: 20 });
let requestSequence = 0;
const columns = [
  { key: "job_type", label: "任务类型" }, { key: "trade_date", label: "交易日" },
  { key: "job_id", label: "任务 ID", minWidth: 220 }, { key: "status", label: "状态" },
  { key: "stage", label: "阶段" }, { key: "progress_current", label: "完成数" },
  { key: "progress_total", label: "总数" }, { key: "success_count", label: "成功" },
  { key: "failure_count", label: "失败" }, { key: "started_at", label: "开始时间", minWidth: 180 },
  { key: "finished_at", label: "结束时间", minWidth: 180 }
];

async function load() {
  const sequence = ++requestSequence;
  loading.value = true;
  try {
    const response = await workbenchApi.jobs(store.tradeDate, query.page, query.pageSize);
    if (sequence !== requestSequence) return;
    rows.value = response.data.items.map((item) => ({ ...item }));
    total.value = response.data.total;
  } finally { if (sequence === requestSequence) loading.value = false; }
}
function changePage(payload: { page: number; pageSize: number }) { query.page = payload.page; query.pageSize = payload.pageSize; void load(); }
watch(() => store.tradeDate, () => { query.page = 1; void load(); }, { immediate: true });
</script>

<style scoped>
.page-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.page-title h1 { margin: 0; font-size: 22px; }
</style>
