import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import MainLayout from "@/layouts/MainLayout.vue";

const routes: RouteRecordRaw[] = [{ path: "/", component: MainLayout, redirect: "/workbench", children: [
  { path: "workbench", component: () => import("@/views/WorkbenchView.vue") },
  { path: "data-status", component: () => import("@/views/DataStatusView.vue") },
  { path: "quant-ranking", component: () => import("@/views/ResultTableView.vue"), props: { kind: "quant" } },
  { path: "llm-screening", component: () => import("@/views/ResultTableView.vue"), props: { kind: "flash" } },
  { path: "manual-selection", component: () => import("@/views/ManualSelectionView.vue") },
  { path: "final-ranking", component: () => import("@/views/ResultTableView.vue"), props: { kind: "final" } },
  { path: "order-position", component: () => import("@/views/ResultTableView.vue"), props: { kind: "orders" } },
  { path: "fundamentals", component: () => import("@/views/ResultTableView.vue"), props: { kind: "fundamentals" } },
  { path: "selection-performance", component: () => import("@/views/SelectionPerformanceView.vue") },
  { path: "runs", component: () => import("@/views/RunHistoryView.vue") },
  { path: "settings", component: () => import("@/views/SettingsView.vue") }
] }, { path: "/:pathMatch(.*)*", redirect: "/workbench" }];
export default createRouter({ history: createWebHistory(), routes });
