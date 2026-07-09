<template>
  <el-alert
    v-if="realTradingEnabled"
    class="safety-banner"
    type="error"
    :closable="false"
    show-icon
    :title="language.t('safety.unsafe')"
  />
  <el-alert
    v-else
    class="safety-banner"
    type="success"
    :closable="false"
    show-icon
    :title="language.t('safety.safe')"
  />
</template>

<script setup lang="ts">
import { useLanguageStore } from "@/stores/languageStore";

defineProps<{
  realTradingEnabled: boolean;
}>();

const language = useLanguageStore();
</script>

<style scoped>
.safety-banner {
  margin-bottom: 14px;
  border-radius: 8px;
}
</style>
