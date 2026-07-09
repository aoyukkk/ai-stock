<template>
  <PageHeader :title="language.t('dataSources.title')">
    <el-button :icon="Refresh" :loading="loading" @click="loadStatus">{{ language.t("common.refresh") }}</el-button>
    <el-button :icon="Search" @click="loadStocks">{{ language.t("dataSources.mockStocks") }}</el-button>
    <el-button :icon="Search" @click="loadQuotes">{{ language.t("dataSources.mockQuotes") }}</el-button>
  </PageHeader>

  <section class="page-section table-wrap">
    <el-table :data="providers" border>
      <el-table-column prop="name" :label="language.t('common.provider')" min-width="140" />
      <el-table-column prop="provider_type" :label="language.t('common.type')" min-width="160" />
      <el-table-column prop="is_mock" label="Mock" width="90" />
      <el-table-column prop="enabled" :label="language.t('common.enabled')" width="100" />
      <el-table-column prop="healthy" :label="language.t('common.healthy')" width="100" />
      <el-table-column prop="status" :label="language.t('common.status')" min-width="140" />
    </el-table>
  </section>

  <section class="page-section">
    <JsonViewer :value="mockData" />
  </section>
</template>

<script setup lang="ts">
import { Refresh, Search } from "@element-plus/icons-vue";
import { onMounted, ref } from "vue";

import { getDataSourceStatus, getMockQuotes, getMockStocks } from "@/api/dataSources";
import JsonViewer from "@/components/JsonViewer.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useLanguageStore } from "@/stores/languageStore";
import { rowsFrom } from "@/utils/data";

const language = useLanguageStore();
const loading = ref(false);
const providers = ref<Record<string, unknown>[]>([]);
const mockData = ref<Record<string, unknown>>({});

async function loadStatus() {
  loading.value = true;
  try {
    const response = await getDataSourceStatus();
    providers.value = rowsFrom(response.data, "providers");
  } finally {
    loading.value = false;
  }
}

async function loadStocks() {
  mockData.value = (await getMockStocks()).data;
}

async function loadQuotes() {
  mockData.value = (await getMockQuotes(["000001", "600519"])).data;
}

onMounted(loadStatus);
</script>
