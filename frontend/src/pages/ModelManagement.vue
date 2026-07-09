<template>
  <PageHeader :title="language.t('nav.modelManagement')" :subtitle="language.t('model.subtitle')">
    <el-button :icon="Refresh" :loading="loading || config.loading" @click="load">{{ language.t("common.refresh") }}</el-button>
  </PageHeader>

  <section class="page-section">
    <el-alert
      type="error"
      :closable="false"
      show-icon
      :title="language.t('model.alert')"
    />
  </section>

  <section class="page-section content-grid">
    <StatusCard :label="language.t('model.mockOnly')" :value="String(configValue('llm.mock_only'))" />
    <StatusCard :label="language.t('model.defaultProvider')" :value="String(configValue('llm.default_provider'))" />
    <StatusCard :label="language.t('model.defaultModel')" :value="String(configValue('llm.default_model'))" />
    <StatusCard :label="language.t('model.tokenBudget')" :value="String(configValue('llm.budgets.daily_token_budget'))" />
    <StatusCard :label="language.t('model.costBudgetUsd')" :value="String(configValue('llm.budgets.daily_cost_budget_usd'))" />
  </section>

  <section class="page-section model-grid">
    <ConfigEditorGroup
      :title="language.categoryLabel('llm')"
      category="llm"
      :items="config.itemsForCategory('llm')"
      @save="saveLlm"
      @reset-item="resetItem"
    />

    <el-card shadow="never">
      <template #header>{{ language.t("model.providerList") }}</template>
      <el-table v-loading="config.loading" :data="providerRows" border>
        <el-table-column prop="provider" :label="language.t('common.provider')" min-width="120" />
        <el-table-column prop="status" :label="language.t('common.status')" min-width="140" />
        <el-table-column :label="language.t('model.selection')" min-width="170">
          <template #default="{ row }">
            <el-select v-model="row.selected" :disabled="row.provider !== 'mock'">
              <el-option label="mock" value="mock" />
              <el-option :label="language.t('model.disabledProvider')" value="disabled" disabled />
            </el-select>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="language.t('model.noProviders')" />
        </template>
      </el-table>
    </el-card>
  </section>

  <section class="page-section model-grid">
    <el-card shadow="never">
      <template #header>{{ language.t("model.agentRoutes") }}</template>
      <el-table :data="routes" border>
        <el-table-column prop="agent" :label="language.t('model.agent')" min-width="160" />
        <el-table-column :label="language.t('common.provider')" min-width="140">
          <template #default="{ row }">
            <el-select v-model="row.provider" disabled>
              <el-option label="mock" value="mock" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column :label="language.t('model.defaultModel')" min-width="160">
          <template #default="{ row }">
            <el-select v-model="row.model" disabled>
              <el-option label="mock-chat" value="mock-chat" />
              <el-option label="mock-fast" value="mock-fast" />
              <el-option label="mock-reasoning" value="mock-reasoning" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column :label="language.t('model.temperature')" width="150">
          <template #default="{ row }"><el-input-number v-model="row.temperature" :min="0" :max="1" :step="0.1" disabled /></template>
        </el-table-column>
        <el-table-column :label="language.t('model.maxTokens')" width="150">
          <template #default="{ row }"><el-input-number v-model="row.maxTokens" :min="256" :step="256" disabled /></template>
        </el-table-column>
      </el-table>
    </el-card>
    <el-card shadow="never">
      <template #header>{{ language.t("model.usageSummary") }}</template>
      <JsonViewer :value="{ status, usage, last_trace_id: config.lastTraceId }" />
    </el-card>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";

import { getLLMStatus, getUsageSummary } from "@/api/llm";
import ConfigEditorGroup from "@/components/ConfigEditorGroup.vue";
import JsonViewer from "@/components/JsonViewer.vue";
import PageHeader from "@/components/PageHeader.vue";
import StatusCard from "@/components/StatusCard.vue";
import { useConfigStore } from "@/stores/configStore";
import { useLanguageStore } from "@/stores/languageStore";

const config = useConfigStore();
const language = useLanguageStore();
const loading = ref(false);
const status = ref<Record<string, unknown>>({});
const usage = ref<Record<string, unknown>>({});
const routes = ref([
  { agent: "technical_agent", provider: "mock", model: "mock-chat", temperature: 0.2, maxTokens: 2000 },
  { agent: "risk_agent", provider: "mock", model: "mock-reasoning", temperature: 0.1, maxTokens: 4000 },
  { agent: "daily_review_agent", provider: "mock", model: "mock-chat", temperature: 0.2, maxTokens: 2000 }
]);

const providerRows = computed(() => [
  { provider: "mock", status: language.t("common.enabled"), selected: "mock" },
  { provider: "openai", status: language.t("model.disabledProvider"), selected: "disabled" },
  { provider: "deepseek", status: language.t("model.disabledProvider"), selected: "disabled" }
]);

function configValue(key: string) {
  return config.effective?.values[key] ?? "-";
}

async function load() {
  loading.value = true;
  try {
    const [llm, llmUsage] = await Promise.all([
      getLLMStatus(),
      getUsageSummary(),
      config.loadConfig()
    ]);
    status.value = llm.data;
    usage.value = llmUsage.data;
  } catch (error) {
    showError(error);
  } finally {
    loading.value = false;
  }
}

async function saveLlm(reason: string) {
  try {
    await ElMessageBox.confirm(language.t("model.saveConfirm"), language.t("systemConfig.saveConfirm"), {
      type: "warning"
    });
    await config.saveCategory("llm", reason);
    ElMessage.success(language.t("model.configSaved"));
  } catch (error) {
    if (error === "cancel" || error === "close") {
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
    if (error === "cancel" || error === "close") {
      return;
    }
    showError(error);
  }
}

function showError(error: unknown) {
  const apiError = error as { message?: string; code?: string; traceId?: string };
  const trace = apiError.traceId ? ` trace_id=${apiError.traceId}` : "";
  const code = apiError.code ? `${apiError.code}: ` : "";
  ElMessage.error(`${code}${apiError.message || language.t("model.error")}${trace}`);
}

onMounted(load);
</script>

<style scoped>
.model-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(300px, 1fr);
  gap: 12px;
}

@media (max-width: 980px) {
  .model-grid {
    grid-template-columns: 1fr;
  }
}
</style>
