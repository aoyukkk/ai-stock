import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

import MainLayout from "@/layouts/MainLayout.vue";

const routes: RouteRecordRaw[] = [
  {
    path: "/",
    component: MainLayout,
    redirect: "/dashboard",
    children: [
      { path: "dashboard", name: "Dashboard", component: () => import("@/pages/Dashboard.vue") },
      { path: "system-status", name: "SystemStatus", component: () => import("@/pages/SystemStatus.vue") },
      { path: "system-config", name: "SystemConfig", component: () => import("@/pages/SystemConfig.vue") },
      { path: "model-management", name: "ModelManagement", component: () => import("@/pages/ModelManagement.vue") },
      { path: "data-sources", name: "DataSources", component: () => import("@/pages/DataSources.vue") },
      { path: "quant-scan", name: "QuantScan", component: () => import("@/pages/QuantScan.vue") },
      { path: "light-screening", name: "LightScreening", component: () => import("@/pages/LightScreening.vue") },
      { path: "committee-ranking", name: "CommitteeRanking", component: () => import("@/pages/CommitteeRanking.vue") },
      { path: "order-price-plans", name: "OrderPricePlans", component: () => import("@/pages/OrderPricePlans.vue") },
      { path: "virtual-trading", name: "VirtualTrading", component: () => import("@/pages/VirtualTrading.vue") },
      { path: "alerts-center", name: "AlertsCenter", component: () => import("@/pages/AlertsCenter.vue") },
      { path: "pre-market-recheck", name: "PreMarketRecheck", component: () => import("@/pages/PreMarketRecheck.vue") },
      { path: "daily-review", name: "DailyReview", component: () => import("@/pages/DailyReview.vue") },
      { path: "memory-console", name: "MemoryConsole", component: () => import("@/pages/MemoryConsole.vue") }
    ]
  },
  { path: "/:pathMatch(.*)*", name: "NotFound", component: () => import("@/pages/NotFound.vue") }
];

export default createRouter({
  history: createWebHistory(),
  routes
});
