<template>
  <PageHeader :title="language.t('nav.dailyReview')">
    <el-date-picker v-model="reviewDate" value-format="YYYY-MM-DD" type="date" />
    <el-button :icon="Refresh" type="primary" :loading="loading" @click="run">{{ language.t("dailyReview.run") }}</el-button>
  </PageHeader>

  <section class="page-section content-grid">
    <StatusCard :label="language.t('dailyReview.predictionAccuracy')" :value="String(review.prediction_accuracy ?? '-')" />
    <StatusCard :label="language.t('dailyReview.orderPriceQuality')" :value="String(review.order_price_quality ?? '-')" />
    <StatusCard :label="language.t('dailyReview.profitLoss')" :value="String(review.profit_loss ?? '-')" />
    <StatusCard :label="language.t('dailyReview.maxDrawdown')" :value="String(review.max_drawdown ?? '-')" />
    <StatusCard :label="language.t('dailyReview.winRate')" :value="String(review.win_rate ?? '-')" />
    <StatusCard :label="language.t('dailyReview.finalReviewScore')" :value="String(review.final_review_score ?? '-')" />
  </section>

  <section class="review-text">
    <el-card shadow="never"><template #header>{{ language.t("dailyReview.mistakeAnalysis") }}</template>{{ review.mistake_analysis || "-" }}</el-card>
    <el-card shadow="never"><template #header>{{ language.t("dailyReview.suggestion") }}</template>{{ review.suggestion || "-" }}</el-card>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import dayjs from "dayjs";
import { ref } from "vue";

import { runDailyReview } from "@/api/review";
import PageHeader from "@/components/PageHeader.vue";
import StatusCard from "@/components/StatusCard.vue";
import { useLanguageStore } from "@/stores/languageStore";

const language = useLanguageStore();
const reviewDate = ref(dayjs().format("YYYY-MM-DD"));
const loading = ref(false);
const review = ref<Record<string, unknown>>({});

async function run() {
  loading.value = true;
  try {
    review.value = (await runDailyReview({ date: reviewDate.value, use_mock_llm: true })).data;
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.review-text {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
}
</style>
