import { createRouter, createWebHashHistory, createWebHistory, type RouteRecordRaw } from "vue-router";
import MainLayout from "@/layouts/MainLayout.vue";
import { useInternalAuthStore } from "@/stores/internalAuth";

const routes: RouteRecordRaw[] = [
  { path: "/first-run", component: () => import("@/views/FirstRunView.vue") },
  { path: "/unauthorized", component: () => import("@/views/AccessDeniedView.vue") },
  { path: "/forbidden", component: () => import("@/views/AccessDeniedView.vue") },
  { path: "/local-login", component: () => import("@/views/LocalLoginView.vue") },
  { path: "/", component: MainLayout, redirect: "/workbench", children: [
  { path: "workbench", component: () => import("@/views/WorkbenchView.vue") },
  { path: "data-status", component: () => import("@/views/DataStatusView.vue") },
  { path: "market-review", component: () => import("@/views/MarketReviewView.vue") },
  { path: "realtime-monitor", component: () => import("@/views/RealtimeMonitorView.vue"), meta: { roles: ["ADMIN", "TRADER"] } },
  { path: "quant-ranking", component: () => import("@/views/ResultTableView.vue"), props: { kind: "quant" } },
  { path: "llm-screening", component: () => import("@/views/ResultTableView.vue"), props: { kind: "flash" } },
  { path: "manual-selection", component: () => import("@/views/ManualSelectionView.vue"), meta: { roles: ["ADMIN", "TRADER"] } },
  { path: "final-ranking", component: () => import("@/views/ResultTableView.vue"), props: { kind: "final" } },
  { path: "entry-timing", component: () => import("@/views/EntryTimingView.vue"), meta: { roles: ["ADMIN", "TRADER"] } },
  { path: "order-position", component: () => import("@/views/ResultTableView.vue"), props: { kind: "orders" } },
  { path: "fundamentals", component: () => import("@/views/ResultTableView.vue"), props: { kind: "fundamentals" } },
  { path: "selection-performance", component: () => import("@/views/SelectionPerformanceView.vue") },
  { path: "post-close-actions", component: () => import("@/views/PostCloseActionsView.vue"), meta: { roles: ["ADMIN", "TRADER"] } },
  { path: "midday-recommendation", component: () => import("@/views/MiddayRecommendationView.vue"), meta: { roles: ["ADMIN", "TRADER"] } },
  { path: "runs", component: () => import("@/views/RunHistoryView.vue") },
  { path: "settings", component: () => import("@/views/SettingsView.vue"), meta: { roles: ["ADMIN"] } }
] }, { path: "/:pathMatch(.*)*", redirect: "/workbench" }];
const router = createRouter({ history: window.aiTraderShell ? createWebHashHistory() : createWebHistory(), routes });
router.beforeEach(async (to) => {
  if (window.aiTraderShell || ["/unauthorized", "/forbidden", "/local-login", "/first-run"].includes(to.path)) return true;
  const auth = useInternalAuthStore();
  try {
    await auth.initialize();
  } catch (error) {
    return (error as { code?: string })?.code === "LOCAL_SESSION_REQUIRED" ? "/local-login" : "/unauthorized";
  }
  const roles = to.meta.roles as string[] | undefined;
  return roles && !roles.includes(auth.role) ? "/forbidden" : true;
});
export default router;
