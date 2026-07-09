<template>
  <PageHeader :title="language.t('nav.systemStatus')">
    <el-button :icon="Refresh" :loading="app.loading" @click="refresh">{{ language.t("common.refresh") }}</el-button>
  </PageHeader>

  <section class="page-section content-grid">
    <StatusCard :label="language.t('systemStatus.realTrading')" :value="String(app.realTradingEnabled)" :detail="app.realTradingEnabled ? language.t('systemStatus.blockedByFrontend') : language.t('common.closed')" />
    <StatusCard :label="language.t('status.api')" :value="app.apiConnected ? language.t('common.connected') : language.t('common.disconnected')" :detail="app.lastError" />
    <StatusCard :label="language.t('status.llm')" :value="language.displayValue(app.llmMode)" :detail="app.mockLlmEnabled ? language.t('systemStatus.mockOnly') : language.t('systemStatus.reviewBackend')" />
    <StatusCard :label="language.t('systemStatus.dataSources')" :value="language.displayValue(app.dataSourceMode)" :detail="app.mockDataEnabled ? language.t('systemStatus.mockProvider') : language.t('systemStatus.reviewBackend')" />
    <StatusCard :label="language.t('systemStatus.deployment')" :value="language.t('systemStatus.localDesktopPrep')" :detail="language.t('systemStatus.deploymentDetail')" />
    <StatusCard :label="language.t('systemStatus.apiBase')" :value="app.apiBaseUrl" :detail="language.t('systemStatus.apiBaseDetail')" />
  </section>

  <section class="page-section status-columns">
    <JsonViewer :value="app.configSummary" />
    <JsonViewer :value="{ health: app.health, database: app.databaseHealth, llm: app.llmStatus, data_sources: app.dataSourceStatus }" />
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";

import JsonViewer from "@/components/JsonViewer.vue";
import PageHeader from "@/components/PageHeader.vue";
import StatusCard from "@/components/StatusCard.vue";
import { useAppStore } from "@/stores/appStore";
import { useLanguageStore } from "@/stores/languageStore";

const app = useAppStore();
const language = useLanguageStore();

function refresh() {
  void app.refreshStatus();
}
</script>

<style scoped>
.status-columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
}
</style>
