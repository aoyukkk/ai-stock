<template>
  <el-container class="workbench-shell">
    <el-aside width="204px" class="workbench-nav">
      <div class="brand">交易员每日筛选工作台</div>
      <el-menu router :default-active="route.path">
        <el-menu-item v-for="item in nav" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span>
          <StatusTag v-if="item.key" class="nav-status" :status="statusFor(item.key)" />
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header height="64px" class="workbench-header">
        <div class="header-status">
          <el-select v-model="store.tradeDate" class="date-select" size="small" placeholder="选择交易日" @change="store.refresh">
            <el-option v-for="item in store.availableDates" :key="item.trade_date" :label="item.trade_date" :value="item.trade_date" />
          </el-select>
          <span>来源：{{ sourceLabel }}</span>
          <span>数据：<StatusTag :status="store.status?.data.status" /></span>
          <span>Quant：<StatusTag :status="store.status?.quant.status" /></span>
          <span>LLM：<StatusTag :status="store.status?.flash.status" /></span>
          <span>终排：<StatusTag :status="store.status?.final.status" /></span>
          <span>Token：{{ number(store.status?.token.used) }} / {{ number(store.status?.token.limit) }}</span>
          <span>真实交易：已关闭</span>
        </div>
        <div class="header-actions">
          <el-select class="language-select" :model-value="language.current" size="small" aria-label="Language" @update:model-value="updateLanguage">
            <el-option v-for="option in language.options" :key="option.value" :label="option.nativeLabel" :value="option.value" />
          </el-select>
          <el-button :icon="FolderOpened" circle title="打开输出目录" aria-label="打开输出目录" />
          <el-button :icon="Setting" circle title="设置" aria-label="设置" @click="$router.push('/settings')" />
          <el-button :icon="Refresh" circle :loading="store.loading" title="刷新状态" aria-label="刷新状态" @click="store.refresh" />
        </div>
      </el-header>
      <el-main class="workbench-main">
        <el-alert v-if="store.error" :title="store.error" type="error" show-icon :closable="false" />
        <RouterView />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { DataAnalysis, Document, FolderOpened, List, Refresh, Setting, Tickets, User, Wallet } from "@element-plus/icons-vue";
import { computed, onMounted } from "vue";
import { useRoute } from "vue-router";

import StatusTag from "@/components/common/StatusTag.vue";
import { useLanguageStore } from "@/stores/languageStore";
import { useWorkbenchStore } from "@/stores/workbench";

const store = useWorkbenchStore();
const language = useLanguageStore();
const route = useRoute();
const nav = computed(() => [
  { path: "/workbench", label: "流程工作台", icon: DataAnalysis, key: "" },
  { path: "/data-status", label: "数据状态", icon: List, key: "data" },
  { path: "/quant-ranking", label: "全 A 量化排名", icon: DataAnalysis, key: "quant" },
  { path: "/llm-screening", label: "LLM 二筛评分", icon: Tickets, key: "flash" },
  { path: "/manual-selection", label: "人工选股", icon: User, key: "manual" },
  { path: "/final-ranking", label: "最终排序", icon: List, key: "final" },
  { path: "/order-position", label: "挂单与仓位", icon: Wallet, key: "final" },
  { path: "/fundamentals", label: "重点基本面", icon: Document, key: "final" },
  { path: "/selection-performance", label: "选股收益统计", icon: DataAnalysis, key: "" },
  { path: "/runs", label: "运行记录", icon: List, key: "" },
  { path: "/settings", label: "设置", icon: Setting, key: "" }
]);
const sourceLabel = computed(() => store.status?.source_mode === "DATABASE" ? "数据库历史结果" : store.status?.source_mode === "MOCK" ? "演示数据" : "无数据");
function statusFor(key: string) { return store.status?.[key as "data" | "quant" | "flash" | "manual" | "final"].status; }
function updateLanguage(value: string) { language.setLanguage(value); }
function number(value?: number): string { return (value ?? 0).toLocaleString("zh-CN"); }
onMounted(() => void store.refresh());
</script>

<style scoped>
.workbench-shell { min-height: 100vh; }
.workbench-nav { background: #fff; border-right: 1px solid #dfe5ec; }
.brand { height: 64px; padding: 20px 16px; color: #17365d; font-weight: 700; border-bottom: 1px solid #e6ebf0; }
.nav-status { margin-left: auto; }
.workbench-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid #dfe5ec; background: #fff; }
.header-status, .header-actions { display: flex; align-items: center; gap: 8px; font-size: 12px; white-space: nowrap; }
.header-status { overflow-x: auto; }
.date-select { width: 132px; }
.language-select { width: 100px; }
.workbench-main { padding: 16px; background: #f4f6f8; }
</style>
