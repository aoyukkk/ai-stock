<template>
  <el-container class="app-shell">
    <el-aside class="sidebar" width="248px">
      <div class="brand">
          <div class="brand__mark">AI</div>
          <div>
            <div class="brand__name">{{ app.appName }}</div>
          <div class="brand__sub">{{ language.t("brand.controlPanel") }}</div>
        </div>
      </div>
      <el-menu router :default-active="route.path" class="nav-menu">
        <el-menu-item v-for="item in navItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="topbar" height="64px">
        <div class="topbar__left">
          <el-tag :type="app.apiConnected ? 'success' : 'danger'" effect="plain">
            {{ language.t("status.api") }} {{ app.apiConnected ? language.t("status.connected") : language.t("status.disconnected") }}
          </el-tag>
          <el-tag :type="app.realTradingEnabled ? 'danger' : 'success'" effect="plain">
            real_trading_enabled={{ app.realTradingEnabled }}
          </el-tag>
          <el-tag effect="plain">{{ language.t("status.llm") }} {{ language.displayValue(app.llmMode) }}</el-tag>
          <el-tag effect="plain">{{ language.t("status.data") }} {{ language.displayValue(app.dataSourceMode) }}</el-tag>
        </div>
        <div class="topbar__right">
          <el-select
            class="language-select"
            :aria-label="language.t('language.label')"
            :model-value="language.current"
            size="small"
            @update:model-value="updateLanguage"
          >
            <el-option
              v-for="option in language.options"
              :key="option.value"
              :label="option.nativeLabel"
              :value="option.value"
            >
              <span>{{ option.nativeLabel }}</span>
              <span class="language-select__meta">{{ option.label }}</span>
            </el-option>
          </el-select>
          <span class="trace mono">{{ app.currentTraceId || language.t("status.noTrace") }}</span>
          <el-button
            :icon="Refresh"
            circle
            :loading="app.loading"
            :title="language.t('common.refresh')"
            :aria-label="language.t('common.refresh')"
            @click="app.refreshStatus"
          />
        </div>
      </el-header>
      <el-main class="main-content">
        <SafetyBanner :real-trading-enabled="app.realTradingEnabled" />
        <el-alert v-if="app.lastError" class="global-error" type="error" :closable="false" show-icon :title="app.lastError" />
        <RouterView />
        <footer class="app-footer">
          <span>{{ app.appName }} v{{ app.appVersion }}</span>
          <span class="mono">{{ app.apiBaseUrl }}</span>
        </footer>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import {
  Bell,
  Connection,
  DataAnalysis,
  DataLine,
  Document,
  House,
  Memo,
  Monitor,
  Refresh,
  Setting,
  Tickets
} from "@element-plus/icons-vue";
import { computed, onMounted } from "vue";
import { useRoute } from "vue-router";

import SafetyBanner from "@/components/SafetyBanner.vue";
import { useAppStore } from "@/stores/appStore";
import { useLanguageStore } from "@/stores/languageStore";

const app = useAppStore();
const language = useLanguageStore();
const route = useRoute();

const navItems = computed(() => [
  { path: "/dashboard", label: language.t("nav.dashboard"), icon: House },
  { path: "/system-status", label: language.t("nav.systemStatus"), icon: Monitor },
  { path: "/system-config", label: language.t("nav.systemConfig"), icon: Setting },
  { path: "/model-management", label: language.t("nav.modelManagement"), icon: DataAnalysis },
  { path: "/data-sources", label: language.t("nav.dataSources"), icon: Connection },
  { path: "/quant-scan", label: language.t("nav.quantScan"), icon: DataLine },
  { path: "/light-screening", label: language.t("nav.lightScreening"), icon: Tickets },
  { path: "/committee-ranking", label: language.t("nav.aiCommittee"), icon: Tickets },
  { path: "/order-price-plans", label: language.t("nav.orderPricePlans"), icon: Document },
  { path: "/virtual-trading", label: language.t("nav.virtualTrading"), icon: Monitor },
  { path: "/alerts-center", label: language.t("nav.alertsCenter"), icon: Bell },
  { path: "/pre-market-recheck", label: language.t("nav.preMarketRecheck"), icon: Refresh },
  { path: "/daily-review", label: language.t("nav.dailyReview"), icon: Memo },
  { path: "/memory-console", label: language.t("nav.memoryConsole"), icon: Memo }
]);

function updateLanguage(value: string) {
  language.setLanguage(value);
}

onMounted(() => {
  void app.refreshStatus();
});
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.sidebar {
  border-right: 1px solid #dfe3eb;
  background: #ffffff;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 64px;
  padding: 0 18px;
  border-bottom: 1px solid #eef0f4;
}

.brand__mark {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 8px;
  background: #1f2937;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
}

.brand__name {
  font-size: 14px;
  font-weight: 700;
}

.brand__sub {
  color: #667085;
  font-size: 12px;
}

.nav-menu {
  border-right: 0;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid #dfe3eb;
  background: #ffffff;
}

.topbar__left,
.topbar__right {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.language-select {
  width: 112px;
}

.language-select__meta {
  float: right;
  margin-left: 12px;
  color: #98a2b3;
  font-size: 12px;
}

.trace {
  max-width: 260px;
  overflow: hidden;
  color: #667085;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.main-content {
  padding: 18px;
  background: #f5f7fb;
}

.global-error {
  margin-bottom: 14px;
  border-radius: 8px;
}

.app-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  margin-top: 22px;
  color: #667085;
  font-size: 12px;
}
</style>
