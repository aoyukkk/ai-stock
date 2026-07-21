// @vitest-environment jsdom

import { shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import JobProgressPanel from "@/components/common/JobProgressPanel.vue";
import { useInternalAuthStore } from "@/stores/internalAuth";
import WorkbenchView from "@/views/WorkbenchView.vue";

describe("WorkbenchView role controls", () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    pinia = createPinia();
    setActivePinia(pinia);
  });

  function render(role: "VIEWER" | "TRADER") {
    useInternalAuthStore().identity = {
      id: 1,
      email: `${role.toLowerCase()}@example.com`,
      display_name: role,
      role,
      auth_mode: "CLOUDFLARE_ACCESS"
    };
    return shallowMount(WorkbenchView, {
      global: {
        plugins: [pinia],
        mocks: { $router: { push: () => undefined } },
        stubs: {
          "el-alert": true,
          "el-button": { template: "<button><slot /></button>" },
          "el-icon": { template: "<span><slot /></span>" }
        }
      }
    });
  }

  it("keeps viewers read-only in the workbench", () => {
    const wrapper = render("VIEWER");
    expect(wrapper.find(".quick-actions").findAll("button")).toHaveLength(1);
    expect(wrapper.findComponent(JobProgressPanel).props("canOperate")).toBe(false);
    expect(wrapper.findAll(".row-actions button").filter((button) => button.text() === "执行")).toHaveLength(0);
    expect(wrapper.findAll(".row-actions button").filter((button) => button.text() === "管理")).toHaveLength(0);
  });

  it("shows workflow actions to traders", () => {
    const wrapper = render("TRADER");
    expect(wrapper.find(".quick-actions").findAll("button")).toHaveLength(4);
    expect(wrapper.findComponent(JobProgressPanel).props("canOperate")).toBe(true);
    expect(wrapper.findAll(".row-actions button").filter((button) => button.text() === "执行")).toHaveLength(6);
    expect(wrapper.findAll(".row-actions button").filter((button) => button.text() === "管理")).toHaveLength(1);
  });
});
