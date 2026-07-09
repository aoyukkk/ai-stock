<template>
  <label class="config-switch">
    <span>{{ label }}</span>
    <el-switch :model-value="modelValue" @update:model-value="emit('update:modelValue', Boolean($event))" />
  </label>
</template>

<script setup lang="ts">
defineProps<{
  label: string;
  modelValue: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
}>();
</script>

<style scoped>
.config-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  color: #344054;
}
</style>
