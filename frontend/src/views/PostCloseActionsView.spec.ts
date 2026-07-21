// @vitest-environment jsdom
import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as postCloseApi from "@/api/postClose";
import { useWorkbenchStore } from "@/stores/workbench";
import PostCloseActionsView from "@/views/PostCloseActionsView.vue";

vi.mock("@/api/postClose", () => ({
  confirmPositionImport: vi.fn(), exportPostClose: vi.fn(), getCurrentPositions: vi.fn(),
  confirmEmptyPositions: vi.fn(), getPositionTruthStatus: vi.fn(),
  getPostCloseCompare: vi.fn(), getPostCloseHistory: vi.fn(), getPostCloseResults: vi.fn(),
  getPostCloseStatus: vi.fn(), previewPositionImport: vi.fn(), runPostCloseFast: vi.fn(),
  runPostClosePro: vi.fn(),
}));

const envelope = (data: unknown) => ({ success: true, data, error: null, trace_id: "test-trace" });

describe("PostCloseActionsView", () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    vi.clearAllMocks();
    pinia = createPinia();
    setActivePinia(pinia);
    useWorkbenchStore().tradeDate = "2026-07-15";
    vi.mocked(postCloseApi.getPostCloseStatus).mockResolvedValue(envelope({ status: "NOT_RUN", trade_date: "2026-07-15" }) as never);
    vi.mocked(postCloseApi.getCurrentPositions).mockResolvedValue(envelope({ items: [], count: 0 }) as never);
    vi.mocked(postCloseApi.getPositionTruthStatus).mockResolvedValue(envelope({ status: "MISSING", human_count: 0, ai_count: 0, confirmed_empty: false, missing_fields: ["HUMAN_REFERENCE:confirmation"], action_run_allowed: false, scope_status: {} }) as never);
    vi.mocked(postCloseApi.getPostCloseHistory).mockResolvedValue(envelope({ items: [], count: 0 }) as never);
  });

  it("loads only the selected trade date and keeps advisory safety text visible", async () => {
    const wrapper = shallowMount(PostCloseActionsView, { global: { plugins: [pinia] } });
    await flushPromises();

    expect(postCloseApi.getPostCloseStatus).toHaveBeenCalledWith(undefined, "2026-07-15");
    expect(wrapper.findAllComponents({ name: "CenteredDataTable" })).toHaveLength(3);
    expect(wrapper.text()).toContain("盘后操作建议");
    expect(wrapper.find("el-alert").attributes("title")).toContain("不会自动卖出、减仓或创建订单");
    expect(wrapper.text()).toContain("使用已有结果");
    expect(wrapper.findAll("el-alert")[1].attributes("title")).toContain("尚未确认持仓");
  });
});
