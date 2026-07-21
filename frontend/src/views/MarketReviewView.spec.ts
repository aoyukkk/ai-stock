// @vitest-environment jsdom
import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { marketReviewApi } from "@/api/marketReview";
import { useWorkbenchStore } from "@/stores/workbench";
import MarketReviewView from "@/views/MarketReviewView.vue";

vi.mock("@/api/marketReview", () => ({
  marketReviewApi: {
    latest: vi.fn(), drivers: vi.fn(), evidence: vi.fn(), run: vi.fn(),
    refreshEvidence: vi.fn(), regenerateSummary: vi.fn(), export: vi.fn(), methodology: vi.fn()
  }
}));

const envelope = (data: unknown) => ({ success: true, data, error: null, trace_id: "test-trace" });
const emptyPage = { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 };

describe("MarketReviewView", () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    vi.clearAllMocks();
    pinia = createPinia();
    setActivePinia(pinia);
    useWorkbenchStore().tradeDate = "2026-07-13";
    vi.mocked(marketReviewApi.latest).mockResolvedValue(envelope({
      run: { status: "DATA_ONLY", market_direction: "DOWN", market_regime: "BROAD_SELL_OFF", search_status: "DATA_ONLY" },
      snapshot: { breadth: { valid_count: 5524 }, turnover: {}, limit_structure: {}, indices: [], industries: [], concepts: [] },
      review: { headline: "2026-07-13 A股市场复盘" }, regime: {}, outlook: {}, search: {}, evidence: [], drivers: [], scenarios: []
    }) as never);
    vi.mocked(marketReviewApi.drivers).mockResolvedValue(envelope(emptyPage) as never);
    vi.mocked(marketReviewApi.evidence).mockResolvedValue(envelope(emptyPage) as never);
  });

  it("loads the selected trade date through mock APIs and renders Chinese review text", async () => {
    const wrapper = shallowMount(MarketReviewView, { global: { plugins: [pinia] } });
    await flushPromises();
    expect(marketReviewApi.latest).toHaveBeenCalledWith("2026-07-13");
    expect(marketReviewApi.drivers).toHaveBeenCalledWith("2026-07-13");
    expect(wrapper.text()).toContain("每日大盘复盘");
    expect(wrapper.text()).toContain("2026-07-13 A股市场复盘");
  });
});
