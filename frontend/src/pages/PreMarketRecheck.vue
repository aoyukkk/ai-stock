<template>
  <PageHeader :title="language.t('nav.preMarketRecheck')">
    <el-input-number v-model="limit" :min="1" controls-position="right" />
    <el-button :icon="Refresh" type="primary" :loading="loading" @click="run">{{ language.t("preMarket.run") }}</el-button>
  </PageHeader>

  <section class="page-section table-wrap">
    <el-table :data="rows" border>
      <el-table-column prop="order_plan_id" :label="language.t('preMarket.planId')" width="100" />
      <el-table-column prop="stock_code" :label="language.t('common.code')" width="110" />
      <el-table-column prop="action" :label="language.t('common.actions')" width="120" />
      <el-table-column prop="old_recommended_price" :label="language.t('preMarket.oldPrice')" width="120" />
      <el-table-column prop="new_recommended_price" :label="language.t('preMarket.newPrice')" width="120" />
      <el-table-column prop="reason" :label="language.t('common.reason')" min-width="320" />
      <el-table-column prop="risk_level" :label="language.t('common.riskLevel')" width="130" />
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { ref } from "vue";

import { runPreMarketRecheck } from "@/api/recheck";
import PageHeader from "@/components/PageHeader.vue";
import { useLanguageStore } from "@/stores/languageStore";
import { rowsFrom } from "@/utils/data";

const language = useLanguageStore();
const limit = ref(20);
const loading = ref(false);
const rows = ref<Record<string, unknown>[]>([]);

async function run() {
  loading.value = true;
  try {
    rows.value = rowsFrom((await runPreMarketRecheck({ limit: limit.value })).data, "results");
  } finally {
    loading.value = false;
  }
}
</script>
