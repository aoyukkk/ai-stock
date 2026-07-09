<template>
  <main class="not-found">
    <h1>{{ language.t("notFound.title") }}</h1>
    <RouterLink to="/dashboard">
      <el-button type="primary">{{ language.t("notFound.back") }}</el-button>
    </RouterLink>
  </main>
</template>

<script setup lang="ts">
import { useLanguageStore } from "@/stores/languageStore";

const language = useLanguageStore();
</script>

<style scoped>
.not-found {
  display: grid;
  min-height: 100vh;
  place-content: center;
  gap: 14px;
  text-align: center;
}
</style>
