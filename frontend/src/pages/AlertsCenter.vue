<template>
  <PageHeader :title="language.t('nav.alertsCenter')">
    <el-button :icon="Refresh" type="primary" :loading="loading" @click="scan">{{ language.t("shortcut.alertsMeta") }}</el-button>
    <el-button :icon="Refresh" @click="loadRecent">{{ language.t("nav.alertsCenter") }}</el-button>
  </PageHeader>

  <section class="page-section table-wrap">
    <el-table :data="alerts" border>
      <el-table-column prop="alert_type" :label="language.t('common.type')" min-width="150" />
      <el-table-column prop="severity" :label="language.t('common.riskLevel')" width="120" />
      <el-table-column prop="stock_code" :label="language.t('common.code')" width="110" />
      <el-table-column prop="message" :label="language.t('common.message')" min-width="280" />
      <el-table-column prop="suggested_action" :label="language.t('common.actions')" width="160" />
      <el-table-column prop="created_at" :label="language.t('common.createdAt')" min-width="180" />
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { onMounted, ref } from "vue";

import { getRecentAlerts, runIntradayScan } from "@/api/alerts";
import PageHeader from "@/components/PageHeader.vue";
import { useLanguageStore } from "@/stores/languageStore";
import { rowsFrom } from "@/utils/data";

const language = useLanguageStore();
const loading = ref(false);
const alerts = ref<Record<string, unknown>[]>([]);

async function scan() {
  loading.value = true;
  try {
    alerts.value = rowsFrom((await runIntradayScan()).data, "alerts");
  } finally {
    loading.value = false;
  }
}

async function loadRecent() {
  alerts.value = rowsFrom((await getRecentAlerts()).data, "alerts");
}

onMounted(loadRecent);
</script>
