<template>
  <PageHeader :title="language.t('nav.memoryConsole')">
    <el-button :icon="Search" :loading="loading" @click="search">{{ language.t("memory.search") }}</el-button>
    <el-button :icon="Plus" type="primary" plain @click="create">{{ language.t("memory.create") }}</el-button>
    <el-button :icon="Refresh" @click="loadPlaybooks">{{ language.t("memory.playbooks") }}</el-button>
  </PageHeader>

  <section class="page-section content-grid">
    <StatusCard :label="language.t('memory.topK')" :value="String(configValue('memory.retrieval.top_k'))" />
    <StatusCard :label="language.t('memory.minQualityScore')" :value="String(configValue('memory.retrieval.min_quality_score'))" />
    <StatusCard :label="language.t('memory.shortTermTtlHours')" :value="String(configValue('memory.short_term.ttl_hours'))" />
    <StatusCard :label="language.t('memory.midTermTtlDays')" :value="String(configValue('memory.mid_term.ttl_days'))" />
    <StatusCard :label="language.t('memory.vectorEnabled')" :value="String(configValue('memory.vector.enabled'))" />
    <StatusCard :label="language.t('memory.graphEnabled')" :value="String(configValue('memory.graph.enabled'))" />
  </section>

  <section class="page-section">
    <el-alert type="info" :closable="false" show-icon :title="language.t('memory.alert')" />
  </section>

  <section class="memory-controls">
    <el-input v-model="query.stock_code" :placeholder="language.t('memory.stockCode')" />
    <el-input v-model="query.keyword" :placeholder="language.t('memory.keyword')" />
    <el-select v-model="query.memory_type" clearable :placeholder="language.t('memory.memoryType')">
      <el-option label="short_term" value="short_term" />
      <el-option label="mid_term" value="mid_term" />
      <el-option label="long_term" value="long_term" />
      <el-option label="reflection" value="reflection" />
      <el-option label="procedural" value="procedural" />
    </el-select>
    <el-input-number v-model="reviewId" :min="1" controls-position="right" />
    <el-button :icon="Plus" @click="reflectionFromReview">{{ language.t("memory.reflectionFromReview") }}</el-button>
  </section>

  <section class="page-section table-wrap">
    <el-table :data="notes" border>
      <el-table-column prop="memory_type" :label="language.t('common.type')" width="130" />
      <el-table-column prop="layer" :label="language.t('common.layer')" width="120" />
      <el-table-column prop="stock_code" :label="language.t('common.code')" width="110" />
      <el-table-column prop="title" :label="language.t('common.title')" min-width="220" />
      <el-table-column prop="summary" :label="language.t('common.summary')" min-width="240" />
      <el-table-column prop="quality_score" :label="language.t('common.quality')" width="110" />
      <el-table-column prop="conflict_status" :label="language.t('memory.conflict')" width="140" />
      <el-table-column prop="should_reuse" :label="language.t('memory.reuse')" width="100" />
      <el-table-column :label="language.t('common.actions')" width="220">
        <template #default="{ row }">
          <el-button size="small" @click="disable(row.id)">{{ language.t("common.disable") }}</el-button>
          <el-button size="small" @click="mark(row.id)">{{ language.t("memory.markConflict") }}</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty :description="language.t('memory.noNotes')" />
      </template>
    </el-table>
  </section>

  <section class="page-section">
    <JsonViewer :value="{ retrieval_log_id: retrievalLogId, playbooks }" />
  </section>
</template>

<script setup lang="ts">
import { Plus, Refresh, Search } from "@element-plus/icons-vue";
import { ref } from "vue";

import { buildReflectionFromReview, createMemory, disableMemory, listPlaybooks, markConflict, searchMemory } from "@/api/memory";
import JsonViewer from "@/components/JsonViewer.vue";
import PageHeader from "@/components/PageHeader.vue";
import StatusCard from "@/components/StatusCard.vue";
import { useConfigStore } from "@/stores/configStore";
import { useLanguageStore } from "@/stores/languageStore";
import { rowsFrom, valueFrom } from "@/utils/data";

const config = useConfigStore();
const language = useLanguageStore();
const loading = ref(false);
const reviewId = ref(1);
const retrievalLogId = ref<unknown>(null);
const query = ref({ stock_code: "000001", keyword: "", memory_type: "" });
const notes = ref<Record<string, unknown>[]>([]);
const playbooks = ref<Record<string, unknown>[]>([]);

async function search() {
  loading.value = true;
  try {
    const payload = {
      stock_code: query.value.stock_code || undefined,
      keyword: query.value.keyword || undefined,
      memory_type: query.value.memory_type || undefined
    };
    const response = await searchMemory(payload);
    notes.value = rowsFrom(response.data, "notes");
    retrievalLogId.value = valueFrom(response.data, "retrieval_log_id", null);
  } finally {
    loading.value = false;
  }
}

async function create() {
  const response = await createMemory({
    agent_name: "frontend_operator",
    stock_code: query.value.stock_code || "000001",
    memory_type: "short_term",
    layer: "stock",
    title: language.t("memory.createTitle"),
    content: language.t("memory.createContent"),
    summary: language.t("memory.createSummary"),
    importance: 60,
    confidence: 60,
    quality_score: 70,
    source_type: "frontend_local"
  });
  notes.value = [response.data, ...notes.value];
}

async function disable(noteId: number) {
  await disableMemory(noteId, "frontend disable");
  await search();
}

async function mark(noteId: number) {
  await markConflict(noteId, "CONFLICTED", "frontend conflict mark");
  await search();
}

async function reflectionFromReview() {
  const response = await buildReflectionFromReview(reviewId.value);
  notes.value = [response.data, ...notes.value];
}

async function loadPlaybooks() {
  playbooks.value = rowsFrom((await listPlaybooks()).data, "playbooks");
}

function configValue(key: string) {
  return config.effective?.values[key] ?? "-";
}

void config.loadConfig().catch(() => undefined);
</script>

<style scoped>
.memory-controls {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
</style>
