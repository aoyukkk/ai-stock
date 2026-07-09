<template>
  <PageHeader :title="language.t('nav.lightScreening')">
    <el-input-number v-model="quantTopQ" :min="1" controls-position="right" />
    <el-input-number v-model="topN" :min="1" controls-position="right" />
    <el-button type="primary" :icon="Refresh" :loading="loading" @click="run">{{ language.t("quant.runLightScreening") }}</el-button>
  </PageHeader>

  <section class="page-section table-wrap">
    <el-table :data="rows" border>
      <el-table-column prop="rank" :label="language.t('common.rank')" width="80" />
      <el-table-column prop="stock_code" :label="language.t('common.code')" width="110" />
      <el-table-column prop="stock_name" :label="language.t('common.name')" min-width="140" />
      <el-table-column prop="quant_total_score" :label="language.t('quant.quant')" width="110" />
      <el-table-column prop="final_light_score" :label="language.t('quant.light')" width="110" />
      <el-table-column prop="direction" :label="language.t('common.direction')" width="110" />
      <el-table-column prop="confidence" :label="language.t('common.confidence')" width="120" />
      <el-table-column prop="reason" :label="language.t('common.reason')" min-width="260" />
      <el-table-column prop="risk_note" :label="language.t('quant.riskNote')" min-width="220" />
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { ref } from "vue";

import { runLightScreening } from "@/api/screening";
import PageHeader from "@/components/PageHeader.vue";
import { useLanguageStore } from "@/stores/languageStore";
import { rowsFrom } from "@/utils/data";

const language = useLanguageStore();
const quantTopQ = ref(50);
const topN = ref(20);
const loading = ref(false);
const rows = ref<Record<string, unknown>[]>([]);

async function run() {
  loading.value = true;
  try {
    rows.value = rowsFrom((await runLightScreening({ quant_top_q: quantTopQ.value, top_n: topN.value, persist: false })).data, "results");
  } finally {
    loading.value = false;
  }
}
</script>
