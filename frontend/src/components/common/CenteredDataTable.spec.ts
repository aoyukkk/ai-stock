// @vitest-environment jsdom
import { shallowMount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { defineComponent } from "vue";

import CenteredDataTable from "./CenteredDataTable.vue";

const props = {
  rows: [{ stock_code: "000001.SZ" }],
  columns: [{ key: "stock_code", label: "股票代码" }],
  currentPage: 1,
  pageSize: 50,
  total: 105,
  paginationEnabled: true
};

function mountTable() {
  const ElPagination = defineComponent({
    name: "ElPagination",
    props: { currentPage: Number, pageSize: Number, pageSizes: Array, total: Number },
    emits: ["current-change", "size-change"],
    template: '<div data-testid="pagination" />'
  });
  return shallowMount(CenteredDataTable, {
    props,
    global: {
      stubs: { ElTable: true, ElTableColumn: true, ElPagination },
      directives: { loading: {} }
    }
  });
}

describe("CenteredDataTable pagination contract", () => {
  it("binds the controlled page and total", () => {
    const wrapper = mountTable();
    const pagination = wrapper.findComponent({ name: "ElPagination" });
    expect(pagination.props("currentPage")).toBe(1);
    expect(pagination.props("pageSize")).toBe(50);
    expect(pagination.props("total")).toBe(105);
  });

  it("emits page 2, next, previous and page 3 changes", async () => {
    const wrapper = mountTable();
    const pagination = wrapper.findComponent({ name: "ElPagination" });
    pagination.vm.$emit("current-change", 2);
    pagination.vm.$emit("current-change", 3);
    pagination.vm.$emit("current-change", 2);
    expect(wrapper.emitted("pagination-change")).toEqual([
      [{ page: 2, pageSize: 50 }],
      [{ page: 3, pageSize: 50 }],
      [{ page: 2, pageSize: 50 }]
    ]);
  });

  it("resets to page 1 when page size changes", () => {
    const wrapper = mountTable();
    wrapper.findComponent({ name: "ElPagination" }).vm.$emit("size-change", 100);
    expect(wrapper.emitted("pagination-change")).toEqual([[{ page: 1, pageSize: 100 }]]);
  });

  it("keeps pagination mounted while the table is loading", async () => {
    const wrapper = mountTable();
    await wrapper.setProps({ loading: true, currentPage: 2, total: 105 });
    const pagination = wrapper.findComponent({ name: "ElPagination" });
    expect(pagination.exists()).toBe(true);
    expect(pagination.props("currentPage")).toBe(2);
  });
});
