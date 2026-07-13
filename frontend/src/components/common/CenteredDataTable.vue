<template>
  <div class="centered-table">
    <el-table
      :data="rows"
      :height="height"
      border
      stripe
      v-loading="loading"
      @sort-change="handleSortChange"
      @selection-change="$emit('selection-change', $event)"
      @row-dblclick="$emit('row-double-click', $event)"
    >
      <el-table-column v-if="selectable" type="selection" width="48" align="center" header-align="center" />
      <el-table-column
        v-for="column in columns"
        :key="column.key"
        :prop="column.key"
        :label="column.label"
        :min-width="column.minWidth ?? 110"
        align="center"
        header-align="center"
      >
        <template #default="scope">
          <slot :name="column.key" :row="scope.row" :value="scope.row[column.key]">
            {{ column.formatter ? column.formatter(scope.row[column.key], scope.row) : format(scope.row[column.key]) }}
          </slot>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="showPagination" class="centered-table__footer">
      <el-pagination
        data-testid="centered-pagination"
        background
        :current-page="currentPage"
        :page-size="pageSize"
        :page-sizes="pageSizes"
        :total="total ?? 0"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="handleCurrentPageChange"
        @size-change="handlePageSizeChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

import type { TableColumn } from "@/types/workbench";

interface PaginationChange {
  page: number;
  pageSize: number;
}

const props = withDefaults(defineProps<{
  rows: Record<string, unknown>[];
  columns: TableColumn[];
  loading?: boolean;
  selectable?: boolean;
  currentPage?: number;
  pageSize?: number;
  total?: number;
  pageSizes?: number[];
  paginationEnabled?: boolean;
  height?: string | number;
}>(), {
  loading: false,
  selectable: false,
  currentPage: 1,
  pageSize: 50,
  pageSizes: () => [20, 50, 100, 200],
  height: "calc(100vh - 230px)"
});

const emit = defineEmits<{
  "pagination-change": [payload: PaginationChange];
  "sort-change": [payload: { prop?: string; order?: string }];
  "selection-change": [rows: Record<string, unknown>[]];
  "row-double-click": [row: Record<string, unknown>];
}>();

const showPagination = computed(() => props.paginationEnabled ?? props.total !== undefined);

function handleCurrentPageChange(page: number) {
  emit("pagination-change", { page, pageSize: props.pageSize });
}

function handlePageSizeChange(pageSize: number) {
  emit("pagination-change", { page: 1, pageSize });
}

function handleSortChange(payload: { prop?: string; order?: string }) {
  emit("sort-change", payload);
}

function format(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (Array.isArray(value)) return value.join("，");
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}
</script>

<style scoped>
.centered-table { width: 100%; }
.centered-table__footer { display: flex; justify-content: center; align-items: center; min-height: 52px; padding: 8px 12px; background: #fff; pointer-events: auto; }
.centered-table :deep(.el-table th.el-table__cell),
.centered-table :deep(.el-table td.el-table__cell) { text-align: center; vertical-align: middle; }
.centered-table :deep(.cell) { display: flex; align-items: center; justify-content: center; min-height: 28px; white-space: normal; overflow-wrap: anywhere; text-align: center; }
</style>
