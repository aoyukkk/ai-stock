<template>
  <el-card class="status-card" shadow="never">
    <div class="status-card__label">{{ label }}</div>
    <div class="status-card__value">{{ value }}</div>
    <div v-if="detail" class="status-card__detail">{{ detail }}</div>
  </el-card>
</template>

<script setup lang="ts">
defineProps<{
  label: string;
  value: string | number;
  detail?: string;
}>();
</script>

<style scoped>
.status-card {
  border-radius: 8px;
}

.status-card__label {
  color: #667085;
  font-size: 13px;
}

.status-card__value {
  margin-top: 6px;
  color: #17202a;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0;
}

.status-card__detail {
  margin-top: 4px;
  color: #667085;
  font-size: 12px;
}
</style>
