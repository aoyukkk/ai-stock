<template>
  <PageHeader :title="language.t('nav.quantScan')">
    <el-input-number v-model="topQ" :min="1" controls-position="right" />
    <el-button type="primary" :icon="Refresh" :loading="loading" @click="run">{{ language.t("quant.runQuantScan") }}</el-button>
  </PageHeader>

  <el-alert
    class="legacy-score-note"
    title="当前正式 Quant 为 TUSHARE_BASELINE_V1 Legacy 未校准分数。0—100 是因子计算范围，不代表收益概率；请优先结合横截面排名，Shadow 版本不会自动晋级生产。"
    type="info"
    show-icon
    :closable="false"
  />

  <section class="page-section table-wrap">
    <el-table :data="rows" border>
      <el-table-column prop="rank" :label="language.t('common.rank')" width="80" />
      <el-table-column prop="stock_code" :label="language.t('common.code')" width="110" />
      <el-table-column prop="stock_name" :label="language.t('common.name')" min-width="140" />
      <el-table-column prop="industry" :label="language.t('quant.industry')" min-width="140" />
      <el-table-column prop="technical_score" :label="language.t('quant.technical')" width="110" />
      <el-table-column prop="capital_score" :label="language.t('quant.capital')" width="110" />
      <el-table-column prop="emotion_score" :label="language.t('quant.emotion')" width="110" />
      <el-table-column prop="momentum_score" :label="language.t('quant.momentum')" width="110" />
      <el-table-column prop="risk_score" :label="language.t('quant.risk')" width="110" />
      <el-table-column prop="total_score" :label="language.t('quant.total')" width="110" />
      <el-table-column prop="reason" :label="language.t('common.reason')" min-width="260" />
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { ref } from "vue";

import { runQuantScan } from "@/api/quant";
import PageHeader from "@/components/PageHeader.vue";
import { useLanguageStore } from "@/stores/languageStore";
import { rowsFrom } from "@/utils/data";

const language = useLanguageStore();
const topQ = ref(50);
const loading = ref(false);
const rows = ref<Record<string, unknown>[]>([]);

async function run() {
  loading.value = true;
  try {
    rows.value = rowsFrom((await runQuantScan({ top_q: topQ.value })).data, "results");
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.legacy-score-note { margin-bottom: 16px; }
</style>
