<template>
  <PageHeader :title="language.t('dashboard.title')" :subtitle="language.t('dashboard.subtitle')" />

  <section class="page-section content-grid">
    <StatusCard :label="language.t('dashboard.backendHealth')" :value="language.displayValue(app.health.status)" :detail="String(app.health.version || '')" />
    <StatusCard :label="language.t('dashboard.database')" :value="language.displayValue(app.databaseHealth.status)" :detail="String(app.databaseHealth.database_type || '')" />
    <StatusCard :label="language.t('dashboard.dataSourceMode')" :value="language.displayValue(app.dataSourceMode)" :detail="app.mockDataEnabled ? language.t('dashboard.mockDataActive') : language.t('dashboard.checkBackend')" />
    <StatusCard :label="language.t('dashboard.llmMode')" :value="language.displayValue(app.llmMode)" :detail="app.mockLlmEnabled ? language.t('dashboard.mockLlmActive') : language.t('dashboard.checkBackend')" />
    <StatusCard :label="language.t('dashboard.startupMode')" :value="language.t('dashboard.localMockOnly')" :detail="language.t('dashboard.startupDetail')" />
    <StatusCard :label="language.t('dashboard.packagingMode')" :value="language.t('dashboard.prepared')" :detail="language.t('dashboard.packagingDetail')" />
  </section>

  <section class="page-section shortcut-grid">
    <RouterLink v-for="item in shortcuts" :key="item.path" :to="item.path">
      <el-card class="shortcut-card" shadow="never">
        <div class="shortcut-card__title">{{ item.title }}</div>
        <div class="shortcut-card__meta">{{ item.meta }}</div>
      </el-card>
    </RouterLink>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";

import PageHeader from "@/components/PageHeader.vue";
import StatusCard from "@/components/StatusCard.vue";
import { useAppStore } from "@/stores/appStore";
import { useLanguageStore } from "@/stores/languageStore";

const app = useAppStore();
const language = useLanguageStore();

const shortcuts = computed(() => [
  { title: language.t("nav.quantScan"), meta: language.t("shortcut.quantMeta"), path: "/quant-scan" },
  { title: language.t("nav.aiCommittee"), meta: language.t("shortcut.committeeMeta"), path: "/committee-ranking" },
  { title: language.t("nav.orderPricePlans"), meta: language.t("shortcut.orderPlansMeta"), path: "/order-price-plans" },
  { title: language.t("shortcut.aiSimulationAccount"), meta: language.t("shortcut.virtualMeta"), path: "/virtual-trading" },
  { title: language.t("nav.alertsCenter"), meta: language.t("shortcut.alertsMeta"), path: "/alerts-center" },
  { title: language.t("nav.dailyReview"), meta: language.t("shortcut.dailyReviewMeta"), path: "/daily-review" },
  { title: language.t("nav.memoryConsole"), meta: language.t("shortcut.memoryMeta"), path: "/memory-console" },
  { title: language.t("nav.systemStatus"), meta: language.t("shortcut.systemStatusMeta"), path: "/system-status" }
]);
</script>

<style scoped>
.shortcut-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.shortcut-card {
  min-height: 92px;
  border-radius: 8px;
  transition: border-color 0.16s ease;
}

.shortcut-card:hover {
  border-color: #7c8aa5;
}

.shortcut-card__title {
  font-size: 16px;
  font-weight: 700;
}

.shortcut-card__meta {
  margin-top: 8px;
  color: #667085;
  font-size: 13px;
}
</style>
