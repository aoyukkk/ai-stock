// @vitest-environment jsdom
import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as realtimeApi from "@/api/realtime";
import { useWorkbenchStore } from "@/stores/workbench";
import RealtimeMonitorView from "@/views/RealtimeMonitorView.vue";

vi.mock("@/api/realtime", () => ({
  actOnMonitorAlert: vi.fn(), confirmMonitorPool: vi.fn(), createMonitorSession: vi.fn(),
  explainMonitorAlert: vi.fn(), getCurrentMonitorSession: vi.fn(), getMiddayMonitorSuggestions: vi.fn(),
  getMonitorAlerts: vi.fn(), getMonitorRules: vi.fn(), getMonitorSessionHistory: vi.fn(), getMonitorUnreadCount: vi.fn(), getMonitorUsage: vi.fn(), getSelectedMonitorPool: vi.fn(),
  getSelectedMonitorResults: vi.fn(), previewMonitorPool: vi.fn(), refreshSelectedMonitor: vi.fn(),
  removeMonitorItem: vi.fn(), transitionMonitorSession: vi.fn(), updateMonitorItem: vi.fn()
}));

const envelope = (data: unknown) => ({ success: true, data, error: null, trace_id: "test" });

describe("RealtimeMonitorView", () => {
  let pinia: ReturnType<typeof createPinia>;
  beforeEach(() => {
    vi.clearAllMocks();
    pinia = createPinia();
    setActivePinia(pinia);
    useWorkbenchStore().tradeDate = "2026-07-16";
    vi.mocked(realtimeApi.getCurrentMonitorSession).mockResolvedValue(envelope(null) as never);
    vi.mocked(realtimeApi.getMonitorSessionHistory).mockResolvedValue(envelope({ items: [], total: 0 }) as never);
  });

  it("does not auto-create or auto-start a session and uses centered tables", async () => {
    const wrapper = shallowMount(RealtimeMonitorView, { global: { plugins: [pinia] } });
    await flushPromises();
    expect(realtimeApi.getCurrentMonitorSession).toHaveBeenCalledWith("2026-07-16");
    expect(realtimeApi.createMonitorSession).not.toHaveBeenCalled();
    expect(realtimeApi.transitionMonitorSession).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("实时盯盘与提醒");
    expect(wrapper.text()).toContain("创建今日 Session");
    expect(wrapper.findAllComponents({ name: "CenteredDataTable" })).toHaveLength(7);
  });
});
