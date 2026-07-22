<template>
  <el-container class="app-shell">
    <el-aside width="232px" class="desktop-sidebar">
      <div class="brand-block">
        <div class="brand-mark">AI</div>
        <div><strong>交易助手</strong><span>每日决策工作台</span></div>
      </div>
      <nav class="sidebar-scroll" aria-label="主导航">
        <el-menu router :default-active="route.path" class="nav-menu">
          <el-menu-item-group v-for="group in navGroups" :key="group.label">
            <template #title><span class="group-title">{{ group.label }}</span></template>
            <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
              <StatusTag v-if="item.key" class="nav-status" :status="statusFor(item.key)" />
            </el-menu-item>
          </el-menu-item-group>
        </el-menu>
      </nav>
      <div class="safety-footer"><span class="safety-dot" />真实交易已关闭</div>
    </el-aside>

    <el-drawer v-model="mobileNavOpen" direction="ltr" size="280px" :with-header="false" class="mobile-drawer">
      <div class="brand-block">
        <div class="brand-mark">AI</div>
        <div><strong>交易助手</strong><span>每日决策工作台</span></div>
      </div>
      <el-menu router :default-active="route.path" class="nav-menu">
        <el-menu-item-group v-for="group in navGroups" :key="group.label">
          <template #title><span class="group-title">{{ group.label }}</span></template>
          <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
            <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
    </el-drawer>

    <el-container class="content-shell">
      <el-header height="72px" class="topbar">
        <div class="page-context">
          <el-button class="mobile-menu" :icon="MenuIcon" circle aria-label="打开导航" @click="mobileNavOpen = true" />
          <div><span>当前页面</span><strong>{{ routeTitle }}</strong></div>
        </div>

        <div class="core-status" aria-label="核心流程状态">
          <div v-for="item in headerStatuses" :key="item.label" class="status-item">
            <span>{{ item.label }}</span><StatusTag :status="item.status" />
          </div>
        </div>

        <div class="top-actions">
          <div v-if="auth.identity" class="identity"><strong>{{ auth.identity.display_name }}</strong><span>{{ auth.identity.role }}</span></div>
          <el-select v-model="store.tradeDate" class="date-select" size="small" placeholder="选择交易日" @change="store.refresh">
            <el-option v-for="item in store.availableDates" :key="item.trade_date" :label="item.trade_date" :value="item.trade_date" />
          </el-select>
          <el-select class="language-select" :model-value="language.current" size="small" aria-label="Language" @update:model-value="updateLanguage">
            <el-option v-for="option in language.options" :key="option.value" :label="option.nativeLabel" :value="option.value" />
          </el-select>
          <el-button v-if="auth.isAdmin" :icon="Setting" circle title="设置" aria-label="设置" @click="$router.push('/settings')" />
          <el-button :icon="Refresh" circle :loading="store.loading" title="刷新状态" aria-label="刷新状态" @click="store.refresh" />
        </div>
      </el-header>

      <div class="mobile-status-row">
        <span>日期 {{ store.tradeDate }}</span><span>数据 <StatusTag :status="store.status?.data.status" /></span><span>终排 <StatusTag :status="store.status?.final.status" /></span>
      </div>
      <el-main class="page-main">
        <el-alert v-if="store.error" class="global-error" :title="store.error" type="error" show-icon :closable="false" />
        <RouterView />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { DataAnalysis, Document, List, Menu as MenuIcon, Monitor, Refresh, Setting, Tickets, TrendCharts, User, Wallet } from "@element-plus/icons-vue";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import StatusTag from "@/components/common/StatusTag.vue";
import { useLanguageStore } from "@/stores/languageStore";
import { useWorkbenchStore } from "@/stores/workbench";
import { useInternalAuthStore } from "@/stores/internalAuth";

type StatusKey = "data" | "quant" | "flash" | "manual" | "final" | "market_review";
const store = useWorkbenchStore();
const language = useLanguageStore();
const route = useRoute();
const auth = useInternalAuthStore();
const mobileNavOpen = ref(false);
const navGroups = computed(() => [
  { label: "日常作业", items: [
    { path: "/workbench", label: "流程工作台", icon: DataAnalysis, key: "" },
    ...(auth.canWrite ? [{ path: "/midday-recommendation", label: "午间推荐", icon: Monitor, key: "" }, { path: "/realtime-monitor", label: "实时盯盘与提醒", icon: Monitor, key: "" }, { path: "/post-close-actions", label: "盘后操作建议", icon: Wallet, key: "" }] : []),
  ] },
  { label: "分析结果", items: [
    { path: "/data-status", label: "数据状态", icon: List, key: "data" },
    { path: "/quant-ranking", label: "全 A 量化排名", icon: DataAnalysis, key: "quant" },
    { path: "/llm-screening", label: "LLM 二筛评分", icon: Tickets, key: "flash" },
    ...(auth.canWrite ? [{ path: "/manual-selection", label: "人工选股", icon: User, key: "manual" }] : []),
    { path: "/final-ranking", label: "最终排序", icon: List, key: "final" },
    ...(auth.canWrite ? [{ path: "/entry-timing", label: "买入准入分析", icon: TrendCharts, key: "" }] : []),
    { path: "/decision-explainability", label: "决策解释", icon: TrendCharts, key: "" },
    { path: "/order-position", label: "挂单与仓位", icon: Wallet, key: "final" },
    { path: "/fundamentals", label: "重点基本面", icon: Document, key: "final" },
  ] },
  { label: "复盘与管理", items: [
    { path: "/market-review", label: "大盘复盘", icon: TrendCharts, key: "market_review" },
    { path: "/selection-performance", label: "选股收益统计", icon: DataAnalysis, key: "" },
    { path: "/runs", label: "运行记录", icon: List, key: "" },
    ...(auth.isAdmin ? [{ path: "/settings", label: "系统设置", icon: Setting, key: "" }] : []),
  ] },
]);
const flatNav = computed(() => navGroups.value.flatMap((group) => group.items));
const routeTitle = computed(() => flatNav.value.find((item) => item.path === route.path)?.label || "交易助手");
const headerStatuses = computed(() => [
  { label: "数据", status: store.status?.data.status },
  { label: "量化", status: store.status?.quant.status },
  { label: "二筛", status: store.status?.flash.status },
  { label: "终排", status: store.status?.final.status },
]);
function statusFor(key: string) { return store.status?.[key as StatusKey].status; }
function updateLanguage(value: string) { language.setLanguage(value); }
watch(() => route.path, () => { mobileNavOpen.value = false; });
onMounted(() => { void auth.initialize(); void store.refresh(); });
</script>

<style scoped>
.app-shell { min-height: 100vh; background: #f3f6f9; }
.desktop-sidebar { position: sticky; top: 0; height: 100vh; background: #fff; border-right: 1px solid #dce3ea; overflow: hidden; }
.brand-block { height: 72px; display: flex; align-items: center; gap: 11px; padding: 0 17px; border-bottom: 1px solid #e7ecf1; }
.brand-mark { width: 36px; height: 36px; display: grid; place-items: center; flex: 0 0 36px; border-radius: 7px; background: #17365d; color: #fff; font-weight: 800; }
.brand-block strong, .brand-block span { display: block; }
.brand-block strong { color: #172333; font-size: 16px; }
.brand-block span { margin-top: 2px; color: #7a8795; font-size: 11px; }
.sidebar-scroll { height: calc(100vh - 116px); overflow-y: auto; padding: 8px 0; scrollbar-width: thin; }
.nav-menu { border-right: 0; }
.nav-menu :deep(.el-menu-item-group__title) { padding: 12px 18px 5px !important; line-height: 20px; }
.group-title { color: #8b97a4; font-size: 11px; font-weight: 700; }
.nav-menu :deep(.el-menu-item) { height: 38px; margin: 2px 9px; padding: 0 11px !important; border-radius: 6px; color: #445263; }
.nav-menu :deep(.el-menu-item.is-active) { background: #eaf2fb; color: #174f87; font-weight: 700; }
.nav-menu :deep(.el-menu-item:hover) { background: #f3f6f9; }
.nav-status { margin-left: auto; transform: scale(.86); transform-origin: right center; }
.safety-footer { height: 44px; display: flex; align-items: center; justify-content: center; gap: 8px; border-top: 1px solid #e7ecf1; color: #3b6b4f; font-size: 12px; }
.safety-dot { width: 7px; height: 7px; border-radius: 50%; background: #36a269; box-shadow: 0 0 0 3px #e0f4e8; }
.content-shell { min-width: 0; }
.topbar { display: grid; grid-template-columns: minmax(150px, 1fr) auto minmax(310px, 1fr); align-items: center; gap: 18px; position: sticky; top: 0; z-index: 20; padding: 0 18px; border-bottom: 1px solid #dce3ea; background: rgba(255,255,255,.97); }
.page-context, .top-actions, .core-status, .status-item { display: flex; align-items: center; }
.page-context { gap: 10px; min-width: 0; }
.page-context span, .status-item > span { display: block; color: #8995a3; font-size: 11px; }
.page-context strong { display: block; margin-top: 2px; color: #182536; font-size: 16px; white-space: nowrap; }
.core-status { justify-content: center; gap: 14px; }
.status-item { gap: 6px; }
.top-actions { justify-content: flex-end; gap: 8px; }
.date-select { width: 128px; }
.language-select { width: 92px; }
.identity { min-width:110px; padding-right:8px; text-align:right; }.identity strong,.identity span{display:block}.identity strong{font-size:12px;color:#24364b}.identity span{font-size:10px;color:#728093}
.mobile-menu, .mobile-status-row { display: none; }
.page-main { min-width: 0; padding: 18px 20px 28px; overflow-x: hidden; }
.global-error { margin-bottom: 12px; }
@media (max-width: 1180px) { .core-status { display: none; } .topbar { grid-template-columns: 1fr auto; } }
@media (max-width: 760px) {
  .desktop-sidebar { display: none; }
  .topbar { height: 62px !important; grid-template-columns: 1fr auto; padding: 0 12px; }
  .mobile-menu { display: inline-flex; }
  .page-context span { display: none; }
  .page-context strong { font-size: 15px; }
  .language-select { display: none; }
  .date-select { width: 112px; }
  .top-actions .el-button:first-of-type { display: none; }
  .mobile-status-row { display: flex; align-items: center; justify-content: center; gap: 12px; min-height: 38px; padding: 5px 10px; overflow-x: auto; border-bottom: 1px solid #e1e7ed; background: #fff; color: #637081; font-size: 11px; white-space: nowrap; }
  .page-main { padding: 12px 10px 22px; }
}
</style>
