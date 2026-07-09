<template>
  <PageHeader :title="language.t('nav.aiCommittee')">
    <el-input-number v-model="inputTopN" :min="1" controls-position="right" />
    <el-input-number v-model="finalTopN" :min="1" controls-position="right" />
    <el-button type="primary" :icon="Refresh" :loading="loading" @click="run">{{ language.t("quant.runCommittee") }}</el-button>
  </PageHeader>

  <section class="page-section table-wrap">
    <el-table :data="rows" border>
      <el-table-column prop="rank" :label="language.t('common.rank')" width="80" />
      <el-table-column prop="stock_code" :label="language.t('common.code')" width="110" />
      <el-table-column prop="stock_name" :label="language.t('common.name')" min-width="140" />
      <el-table-column prop="final_score" :label="language.t('quant.finalScore')" width="120" />
      <el-table-column prop="recommendation" :label="language.t('quant.recommendation')" width="150" />
      <el-table-column prop="risk_level" :label="language.t('common.riskLevel')" width="130" />
      <el-table-column prop="confidence" :label="language.t('common.confidence')" width="120" />
      <el-table-column prop="controller_reason" :label="language.t('quant.controllerReason')" min-width="320" />
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { ref } from "vue";

import { runCommittee } from "@/api/committee";
import PageHeader from "@/components/PageHeader.vue";
import { useLanguageStore } from "@/stores/languageStore";
import { rowsFrom } from "@/utils/data";

const language = useLanguageStore();
const inputTopN = ref(20);
const finalTopN = ref(10);
const loading = ref(false);
const rows = ref<Record<string, unknown>[]>([]);

async function run() {
  loading.value = true;
  try {
    rows.value = rowsFrom((await runCommittee({ input_top_n: inputTopN.value, final_top_n: finalTopN.value, persist: false })).data, "results");
  } finally {
    loading.value = false;
  }
}
</script>
