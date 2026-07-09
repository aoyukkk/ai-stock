<template>
  <PageHeader :title="language.t('nav.orderPricePlans')">
    <el-input-number v-model="inputTopN" :min="1" controls-position="right" />
    <el-button type="primary" :icon="Refresh" :loading="loading" @click="run">{{ language.t("order.generate") }}</el-button>
  </PageHeader>

  <section class="page-section table-wrap">
    <el-table :data="rows" border>
      <el-table-column type="expand">
        <template #default="{ row }">
          <JsonViewer :value="row.candidates || []" />
        </template>
      </el-table-column>
      <el-table-column prop="stock_code" :label="language.t('common.code')" width="110" />
      <el-table-column prop="stock_name" :label="language.t('common.name')" min-width="140" />
      <el-table-column prop="recommended_price" :label="language.t('order.recommended')" width="130" />
      <el-table-column prop="price_range_low" :label="language.t('order.low')" width="110" />
      <el-table-column prop="price_range_high" :label="language.t('order.high')" width="110" />
      <el-table-column prop="max_acceptable_price" :label="language.t('order.maxAcceptable')" width="150" />
      <el-table-column prop="stop_loss_price" :label="language.t('order.stopLoss')" width="120" />
      <el-table-column prop="take_profit_1_price" :label="language.t('order.takeProfit1')" width="140" />
      <el-table-column prop="take_profit_2_price" :label="language.t('order.takeProfit2')" width="140" />
      <el-table-column prop="status" :label="language.t('common.status')" width="120" />
      <el-table-column prop="reason" :label="language.t('common.reason')" min-width="260" />
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { ref } from "vue";

import { getOrderPricePlans } from "@/api/orderPrice";
import JsonViewer from "@/components/JsonViewer.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useLanguageStore } from "@/stores/languageStore";
import { rowsFrom } from "@/utils/data";

const language = useLanguageStore();
const inputTopN = ref(10);
const loading = ref(false);
const rows = ref<Record<string, unknown>[]>([]);

async function run() {
  loading.value = true;
  try {
    rows.value = rowsFrom((await getOrderPricePlans({ input_top_n: inputTopN.value, persist: false })).data, "plans");
  } finally {
    loading.value = false;
  }
}
</script>
