<template>
  <pre class="json-viewer">{{ serialized }}</pre>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { sanitizeSensitive } from "@/api/http";

const props = defineProps<{
  value: unknown;
}>();

const serialized = computed(() => JSON.stringify(sanitizeSensitive(props.value ?? {}), null, 2));
</script>

<style scoped>
.json-viewer {
  width: 100%;
  max-height: 360px;
  margin: 0;
  overflow: auto;
  padding: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #101828;
  color: #d0d5dd;
  font-size: 12px;
  line-height: 1.5;
}
</style>
