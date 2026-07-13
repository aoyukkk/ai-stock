<template>
  <div class="charts">
    <section>
      <h3>各选股日组合累计收益</h3>
      <svg viewBox="0 0 600 180" role="img" aria-label="各选股日组合累计收益曲线">
        <line x1="36" y1="90" x2="580" y2="90" class="axis" />
        <polyline v-for="line in lines" :key="line.key" :points="line.points" :class="line.positive ? 'positive' : 'negative'" />
      </svg>
      <div class="legend"><span v-for="line in lines" :key="line.key">{{ line.key }}</span></div>
    </section>
    <section>
      <h3>选股日最终累计表现</h3>
      <div class="bars">
        <div v-for="item in bars" :key="item.label" class="bar-item">
          <div class="bar-track"><div class="bar" :class="item.value >= 0 ? 'up' : 'down'" :style="{ height: `${Math.max(4, Math.min(100, Math.abs(item.value) * 500))}%` }" /></div>
          <strong :class="item.value >= 0 ? 'return-up' : 'return-down'">{{ percent(item.value) }}</strong>
          <span>{{ item.label }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ daily: Record<string, unknown>[]; cohorts: Record<string, unknown>[] }>();
const lines = computed(() => {
  const groups = new Map<string, Record<string, unknown>[]>();
  props.daily.forEach((row) => {
    const key = String(row.selection_trade_date);
    groups.set(key, [...(groups.get(key) ?? []), row]);
  });
  return [...groups.entries()].slice(-5).map(([key, rows]) => {
    const values = rows.map((row) => Number(row.cumulative_return ?? 0));
    const points = values.map((value, index) => `${36 + index * (544 / Math.max(1, values.length - 1))},${90 - value * 400}`).join(" ");
    return { key, points, positive: (values.at(-1) ?? 0) >= 0 };
  });
});
const bars = computed(() => props.cohorts.map((row) => ({ label: String(row.selection_trade_date), value: Number(row.cumulative_return ?? 0) })));
const percent = (value: number) => `${(value * 100).toFixed(2)}%`;
</script>

<style scoped>
.charts { display: grid; grid-template-columns: 1.4fr 1fr; gap: 12px; margin: 12px 0; }
.charts section { min-width: 0; padding: 12px; border: 1px solid #dfe5ec; background: #fff; }
h3 { margin: 0 0 8px; font-size: 15px; text-align: center; }
svg { width: 100%; height: 180px; }
.axis { stroke: #b8c0cc; stroke-width: 1; }
polyline { fill: none; stroke-width: 2.5; }
polyline.positive { stroke: #c0392b; }
polyline.negative { stroke: #17834b; }
.legend { display: flex; justify-content: center; flex-wrap: wrap; gap: 12px; font-size: 12px; }
.bars { display: flex; align-items: end; justify-content: center; gap: 14px; height: 190px; }
.bar-item { display: grid; grid-template-rows: 130px 22px 20px; min-width: 66px; text-align: center; font-size: 11px; }
.bar-track { display: flex; align-items: end; justify-content: center; border-bottom: 1px solid #b8c0cc; }
.bar { width: 30px; }
.bar.up { background: #d84a3a; }
.bar.down { background: #2d9b61; }
</style>
