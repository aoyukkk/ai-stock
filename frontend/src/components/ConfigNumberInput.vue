<template>
  <label class="config-field">
    <span>{{ label }}</span>
    <el-input-number
      :model-value="modelValue"
      :min="min"
      :max="max"
      :step="step"
      controls-position="right"
      @update:model-value="emit('update:modelValue', Number($event || 0))"
    />
  </label>
</template>

<script setup lang="ts">
defineProps<{
  label: string;
  modelValue: number;
  min?: number;
  max?: number;
  step?: number;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: number];
}>();
</script>

<style scoped>
.config-field {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) 180px;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  color: #344054;
}
</style>
