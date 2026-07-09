<template>
  <PageHeader :title="language.t('systemConfig.title')" :subtitle="language.t('systemConfig.subtitle')">
    <el-button :icon="Refresh" :loading="config.loading" @click="reload">{{ language.t("common.refresh") }}</el-button>
  </PageHeader>

  <section v-if="config.lastError" class="page-section">
    <el-alert type="error" :closable="false" show-icon :title="config.lastError" />
  </section>

  <section v-if="config.realTradingEnabled" class="page-section">
    <el-alert type="error" :closable="false" show-icon :title="language.t('systemConfig.errorRealTrading')" />
  </section>

  <section class="page-section config-summary">
    <StatusCard :label="language.t('systemConfig.priority')" :value="priorityLabel" />
    <StatusCard :label="language.t('systemConfig.llmMode')" :value="String(config.effective?.llm_mock_only ? 'mock_only' : 'blocked')" />
    <StatusCard :label="language.t('systemConfig.dataSource')" :value="String(config.effective?.data_source_mode || 'mock_only')" />
    <StatusCard :label="language.t('systemConfig.realTrading')" :value="String(config.effective?.real_trading_enabled ?? false)" />
  </section>

  <section class="page-section">
    <el-tabs v-model="activeCategory" class="config-tabs">
      <el-tab-pane
        v-for="category in visibleCategories"
        :key="category"
        :label="categoryLabel(category)"
        :name="category"
      >
        <ConfigEditorGroup
          :title="categoryLabel(category)"
          :category="category"
          :items="config.itemsForCategory(category)"
          :danger="category === 'risk_alert'"
          @save="(reason) => saveCategory(category, reason)"
          @reset-item="resetItem"
        />
      </el-tab-pane>
    </el-tabs>
  </section>

  <section class="page-section history-section">
    <div class="history-section__header">
      <h2>{{ language.t("systemConfig.configHistory") }}</h2>
      <el-button :icon="Refresh" :loading="config.loading" @click="loadHistory">{{ language.t("common.refresh") }}</el-button>
    </div>
    <div class="table-wrap">
      <el-table :data="config.history" border>
        <el-table-column prop="time" :label="language.t('systemConfig.time')" width="210" />
        <el-table-column prop="config_key" :label="language.t('systemConfig.key')" min-width="260" />
        <el-table-column prop="old_value" :label="language.t('systemConfig.old')" min-width="120" />
        <el-table-column prop="new_value" :label="language.t('systemConfig.new')" min-width="120" />
        <el-table-column prop="user" :label="language.t('common.user')" width="140" />
        <el-table-column prop="reason" :label="language.t('common.reason')" min-width="220" />
        <template #empty>
          <el-empty :description="language.t('systemConfig.noHistory')" />
        </template>
      </el-table>
    </div>
  </section>

  <section class="page-section">
    <JsonViewer :value="{ effective: config.effective, last_trace_id: config.lastTraceId }" />
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";

import ConfigEditorGroup from "@/components/ConfigEditorGroup.vue";
import JsonViewer from "@/components/JsonViewer.vue";
import PageHeader from "@/components/PageHeader.vue";
import StatusCard from "@/components/StatusCard.vue";
import { categoryOrder, useConfigStore } from "@/stores/configStore";
import { useLanguageStore } from "@/stores/languageStore";

const config = useConfigStore();
const language = useLanguageStore();
const activeCategory = ref("stock_scan");

const visibleCategories = computed(() => {
  const existing = categoryOrder.filter((category) => config.itemsForCategory(category).length);
  return existing.length ? existing : categoryOrder;
});

const priorityLabel = computed(() => (config.effective?.priority || []).join(" > ") || "-");

function categoryLabel(category: string): string {
  return language.categoryLabel(category);
}

async function reload() {
  try {
    await config.loadConfig();
    await config.loadHistory();
  } catch (error) {
    showError(error);
  }
}

async function loadHistory() {
  try {
    await config.loadHistory();
  } catch (error) {
    showError(error);
  }
}

async function saveCategory(category: string, reason: string) {
  try {
    await ElMessageBox.confirm(
      `${language.t("systemConfig.savePromptPrefix")} ${categoryLabel(category)} ${language.t("systemConfig.savePromptSuffix")}`,
      language.t("systemConfig.saveConfirm"),
      { type: "warning" }
    );
    await config.saveCategory(category, reason);
    ElMessage.success(language.t("systemConfig.saveSuccess"));
  } catch (error) {
    if (isCancel(error)) {
      return;
    }
    showError(error);
  }
}

async function resetItem(configKey: string) {
  try {
    await ElMessageBox.confirm(
      `${language.t("systemConfig.resetPromptPrefix")} ${configKey} ${language.t("systemConfig.resetPromptSuffix")}`,
      language.t("systemConfig.resetConfirm"),
      { type: "warning" }
    );
    await config.resetItem(configKey);
    ElMessage.success(language.t("systemConfig.resetSuccess"));
  } catch (error) {
    if (isCancel(error)) {
      return;
    }
    showError(error);
  }
}

function showError(error: unknown) {
  const apiError = error as { message?: string; code?: string; traceId?: string };
  const trace = apiError.traceId ? ` trace_id=${apiError.traceId}` : "";
  const code = apiError.code ? `${apiError.code}: ` : "";
  ElMessage.error(`${code}${apiError.message || language.t("systemConfig.error")}${trace}`);
}

function isCancel(error: unknown): boolean {
  return error === "cancel" || error === "close";
}

onMounted(() => {
  void reload();
});
</script>

<style scoped>
.config-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.config-tabs :deep(.el-tabs__content) {
  overflow: visible;
}

.history-section__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.history-section__header h2 {
  margin: 0;
  color: #344054;
  font-size: 16px;
  letter-spacing: 0;
}
</style>
