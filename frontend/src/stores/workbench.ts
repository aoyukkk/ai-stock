import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { workbenchApi } from "@/api/workbench";
import type { AvailableTradeDate, PipelineJob, WorkbenchStatus } from "@/types/workbench";

const ACTIVE_JOB_STATUSES = new Set(["PENDING", "RUNNING", "CANCELLATION_REQUESTED"]);
const RESUMABLE_JOB_STATUSES = new Set(["FAILED", "CANCELLED", "PARTIAL_SUCCESS"]);
const POLL_INTERVAL_MS = 1500;

export function isActiveJob(job?: PipelineJob | null): boolean {
  return Boolean(job && ACTIVE_JOB_STATUSES.has(job.status));
}

export function isResumableJob(job?: PipelineJob | null): boolean {
  return Boolean(job && RESUMABLE_JOB_STATUSES.has(job.status));
}

function localTradeDate(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export const useWorkbenchStore = defineStore("workbench", () => {
  const tradeDate = ref(localTradeDate());
  const status = ref<WorkbenchStatus | null>(null);
  const availableDates = ref<AvailableTradeDate[]>([]);
  const jobs = ref<PipelineJob[]>([]);
  const currentJob = ref<PipelineJob | null>(null);
  const settings = ref<Record<string, unknown>>({});
  const loading = ref(false);
  const error = ref("");
  const hasActiveJob = computed(() => isActiveJob(currentJob.value));
  let requestSequence = 0;
  let activeController: AbortController | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let monitoredJobId = "";

  function upsertJob(job: PipelineJob) {
    const index = jobs.value.findIndex((item) => item.job_id === job.job_id);
    if (index >= 0) jobs.value[index] = job;
    else jobs.value.unshift(job);
  }

  function stopMonitoring() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
    monitoredJobId = "";
  }

  function monitorJob(jobId: string) {
    if (monitoredJobId === jobId && pollTimer) return;
    stopMonitoring();
    monitoredJobId = jobId;

    const poll = async () => {
      try {
        const response = await workbenchApi.job(jobId);
        if (monitoredJobId !== jobId) return;
        currentJob.value = response.data;
        upsertJob(response.data);
        if (isActiveJob(response.data)) {
          pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
          return;
        }
        stopMonitoring();
        await refresh();
      } catch (cause) {
        if (monitoredJobId !== jobId) return;
        error.value = cause instanceof Error ? cause.message : "无法读取任务进度";
        pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    };
    void poll();
  }

  async function refresh() {
    const sequence = ++requestSequence;
    if (currentJob.value && currentJob.value.trade_date !== tradeDate.value) {
      stopMonitoring();
      currentJob.value = null;
    }
    status.value = null;
    activeController?.abort();
    const controller = new AbortController();
    activeController = controller;
    loading.value = true;
    error.value = "";
    try {
      const dateResponse = await workbenchApi.availableDates(controller.signal);
      if (sequence !== requestSequence) return;
      availableDates.value = dateResponse.data.items;
      if (!availableDates.value.some((item) => item.trade_date === tradeDate.value) && availableDates.value.length) {
        tradeDate.value = availableDates.value[0].trade_date;
      }
      const selectedDate = tradeDate.value;
      const [statusResponse, jobsResponse, settingsResponse] = await Promise.all([
        workbenchApi.status(selectedDate, null, controller.signal),
        workbenchApi.jobs(selectedDate, 1, 20, controller.signal),
        workbenchApi.settings()
      ]);
      if (sequence !== requestSequence || selectedDate !== tradeDate.value) return;
      status.value = statusResponse.data;
      jobs.value = jobsResponse.data.items;
      settings.value = settingsResponse.data;
      const running = jobs.value.find(isActiveJob);
      currentJob.value = running || jobs.value[0] || null;
      if (running) monitorJob(running.job_id);
      else if (monitoredJobId) stopMonitoring();
    } catch (cause) {
      if (controller.signal.aborted) return;
      error.value = cause instanceof Error ? cause.message : "无法读取工作台状态";
    } finally {
      if (sequence === requestSequence) loading.value = false;
    }
  }

  async function run(type: "data" | "quant" | "flash" | "final" | "export", confirmBudget = false) {
    const response = await workbenchApi.run(type, tradeDate.value, confirmBudget);
    currentJob.value = response.data;
    upsertJob(response.data);
    if (isActiveJob(response.data)) monitorJob(response.data.job_id);
    else await refresh();
    return response.data;
  }

  async function resume(jobId: string) {
    const response = await workbenchApi.resumeJob(jobId);
    currentJob.value = response.data;
    upsertJob(response.data);
    if (isActiveJob(response.data)) monitorJob(response.data.job_id);
    return response.data;
  }

  async function cancel(jobId: string) {
    const response = await workbenchApi.cancelJob(jobId);
    currentJob.value = response.data;
    upsertJob(response.data);
    return response.data;
  }

  return {
    tradeDate, status, availableDates, jobs, currentJob, settings, loading, error, hasActiveJob,
    refresh, run, resume, cancel, stopMonitoring
  };
});
