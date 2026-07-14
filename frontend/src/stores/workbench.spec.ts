// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PipelineJob } from "@/types/workbench";

const apiMocks = vi.hoisted(() => ({
  run: vi.fn(), job: vi.fn(), resumeJob: vi.fn(), cancelJob: vi.fn(),
  availableDates: vi.fn(), status: vi.fn(), jobs: vi.fn(), settings: vi.fn()
}));

vi.mock("@/api/workbench", () => ({ workbenchApi: apiMocks }));

import { isActiveJob, isResumableJob, useWorkbenchStore } from "./workbench";

function job(status: PipelineJob["status"], progress = 0): PipelineJob {
  return {
    job_id: "job-flash", job_type: "FLASH", trade_date: "2026-07-13", status,
    stage: status === "SUCCESS" ? "COMPLETED" : "FUNDAMENTAL_V4_FLASH_V5",
    progress_current: progress, progress_total: 100, success_count: progress,
    failure_count: 0, token_usage: progress * 100, cost_usd: 0,
    started_at: null, finished_at: null, run_ids: {}
  };
}

describe("workbench persistent job monitoring", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    apiMocks.availableDates.mockResolvedValue({ data: { items: [{
      trade_date: "2026-07-13", pipeline_status: "SUCCESS", pipeline_run_count: 1,
      latest_completed_at: "2026-07-13T10:00:00Z"
    }] } });
    apiMocks.status.mockResolvedValue({ data: {} });
    apiMocks.jobs.mockResolvedValue({ data: { items: [job("SUCCESS", 100)], total: 1 } });
    apiMocks.settings.mockResolvedValue({ data: {
      quant_top_n: 5000, llm_analysis_n: 100, llm_top_n: 20, daily_token_limit: 5000000
    } });
  });

  afterEach(() => vi.useRealTimers());

  it("classifies active and resumable jobs", () => {
    expect(isActiveJob(job("RUNNING", 10))).toBe(true);
    expect(isActiveJob(job("SUCCESS", 100))).toBe(false);
    expect(isResumableJob(job("FAILED", 40))).toBe(true);
    expect(isResumableJob(job("CANCELLED", 40))).toBe(true);
  });

  it("polls a submitted Flash job until completion", async () => {
    apiMocks.run.mockResolvedValue({ data: job("PENDING", 0) });
    apiMocks.job
      .mockResolvedValueOnce({ data: job("RUNNING", 10) })
      .mockResolvedValueOnce({ data: job("SUCCESS", 100) });
    const store = useWorkbenchStore();

    await store.run("flash", true);
    await Promise.resolve();
    expect(apiMocks.job).toHaveBeenCalledTimes(1);
    expect(store.currentJob?.status).toBe("RUNNING");
    expect(store.currentJob?.progress_current).toBe(10);

    await vi.advanceTimersByTimeAsync(1500);
    await Promise.resolve();
    expect(apiMocks.job).toHaveBeenCalledTimes(2);
    expect(store.currentJob?.status).toBe("SUCCESS");
  });

  it("starts monitoring the new job returned by resume", async () => {
    apiMocks.resumeJob.mockResolvedValue({ data: { ...job("PENDING", 40), job_id: "job-resumed" } });
    apiMocks.job.mockResolvedValue({ data: { ...job("RUNNING", 50), job_id: "job-resumed" } });
    const store = useWorkbenchStore();
    await store.resume("job-failed");
    await Promise.resolve();
    expect(apiMocks.resumeJob).toHaveBeenCalledWith("job-failed");
    expect(apiMocks.job).toHaveBeenCalledWith("job-resumed");
    store.stopMonitoring();
  });

  it("clears a job from another trade date when the selected date has no jobs", async () => {
    const store = useWorkbenchStore();
    store.currentJob = { ...job("SUCCESS", 100), trade_date: "2026-07-10" };
    store.tradeDate = "2026-07-13";
    apiMocks.jobs.mockResolvedValue({ data: { items: [], total: 0 } });

    await store.refresh();

    expect(store.currentJob).toBeNull();
  });
});
