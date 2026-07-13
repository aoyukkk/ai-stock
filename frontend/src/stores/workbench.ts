import { defineStore } from "pinia";
import { ref } from "vue";

import { workbenchApi } from "@/api/workbench";
import type { AvailableTradeDate, PipelineJob, WorkbenchStatus } from "@/types/workbench";

export const useWorkbenchStore = defineStore("workbench", () => {
  const tradeDate = ref("2026-07-10");
  const status = ref<WorkbenchStatus | null>(null);
  const availableDates = ref<AvailableTradeDate[]>([]);
  const jobs = ref<PipelineJob[]>([]);
  const loading = ref(false);
  const error = ref("");
  let requestSequence = 0;
  let activeController: AbortController | null = null;

  async function refresh() {
    const sequence = ++requestSequence;
    activeController?.abort();
    const controller = new AbortController();
    activeController = controller;
    loading.value = true;
    error.value = "";
    status.value = null;
    jobs.value = [];
    try {
      const dateResponse = await workbenchApi.availableDates(controller.signal);
      if (sequence !== requestSequence) return;
      availableDates.value = dateResponse.data.items;
      if (!availableDates.value.some((item) => item.trade_date === tradeDate.value) && availableDates.value.length) {
        tradeDate.value = availableDates.value[0].trade_date;
      }
      const selectedDate = tradeDate.value;
      const [statusResponse, jobsResponse] = await Promise.all([
        workbenchApi.status(selectedDate, null, controller.signal),
        workbenchApi.jobs(selectedDate)
      ]);
      if (sequence !== requestSequence || selectedDate !== tradeDate.value) return;
      status.value = statusResponse.data;
      jobs.value = jobsResponse.data.items;
    } catch (cause) {
      if (controller.signal.aborted) return;
      error.value = cause instanceof Error ? cause.message : "无法读取工作台状态";
    } finally {
      if (sequence === requestSequence) loading.value = false;
    }
  }

  async function run(type: "data" | "quant" | "flash" | "final" | "export") {
    void type;
    await workbenchApi.loadExisting(tradeDate.value, status.value?.pipeline_run_id);
    await refresh();
  }

  return { tradeDate, status, availableDates, jobs, loading, error, refresh, run };
});
