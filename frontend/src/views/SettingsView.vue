<template>
  <section>
    <div class="page-title"><h1>设置</h1><el-button type="primary" @click="saveAll">保存设置</el-button></div>
    <div class="settings-grid">
      <el-card shadow="never">
        <template #header>筛选数量</template>
        <el-form label-width="150px"><el-form-item v-for="field in screeningFields" :key="field.key" :label="field.label"><el-input-number v-model="workbench[field.key]" :min="field.min" :max="field.max" /></el-form-item></el-form>
      </el-card>
      <el-card shadow="never">
        <template #header>收益统计</template>
        <el-form label-width="180px">
          <el-form-item label="默认向前交易日数"><el-input-number v-model="performance.default_lookback_value" :min="1" :max="120" /></el-form-item>
          <el-form-item label="最大向前交易日数"><el-input-number v-model="performance.max_lookback_value" :min="1" :max="120" /></el-form-item>
          <el-form-item label="默认收益起算方式"><el-select v-model="performance.default_return_basis"><el-option label="次日开盘" value="NEXT_OPEN" /><el-option label="信号收盘" value="SIGNAL_CLOSE" /></el-select></el-form-item>
          <el-form-item label="默认选股范围"><el-select v-model="performance.default_selection_scope"><el-option label="全部最终候选" value="FINAL_CANDIDATES" /><el-option label="仅LLM" value="LLM_ONLY" /><el-option label="仅人工" value="MANUAL_ONLY" /><el-option label="BOTH" value="BOTH_ONLY" /><el-option label="非零仓位" value="NON_ZERO_POSITION" /></el-select></el-form-item>
          <el-form-item label="默认加权方式"><el-select v-model="performance.default_weighting_mode"><el-option label="等权" value="EQUAL_WEIGHT" /><el-option label="建议仓位" value="SUGGESTED_POSITION_WEIGHT" /></el-select></el-form-item>
          <el-form-item label="最低覆盖率"><el-input-number v-model="performance.min_coverage_ratio" :min="0" :max="1" :step="0.01" :precision="2" /></el-form-item>
          <el-form-item label="数据完成后自动刷新"><el-switch v-model="performance.auto_refresh_after_data_ready" /></el-form-item>
          <el-form-item label="包含零仓位股票"><el-switch v-model="performance.include_zero_position_stocks" /></el-form-item>
          <el-form-item label="包含风险阻断股票"><el-switch v-model="performance.include_risk_blocked_stocks" /></el-form-item>
          <el-form-item label="自动导出收益Excel"><el-switch v-model="performance.auto_export_excel" /></el-form-item>
        </el-form>
      </el-card>
      <el-card shadow="never" class="secrets">
        <template #header>本地 API Key</template>
        <div v-for="provider in providers" :key="provider" class="secret-row">
          <span>{{ provider }}</span><StatusTag :status="secretStatus[provider]?.configured ? 'READY' : 'NOT_RUN'" />
          <el-input v-model="secretInput[provider]" type="password" show-password placeholder="提交后立即清空" />
          <el-button @click="saveSecret(provider)">更新</el-button><el-button @click="testSecret(provider)">测试</el-button><el-button type="danger" plain :disabled="!secretStatus[provider]?.configured" @click="deleteSecret(provider)">删除</el-button>
        </div>
        <p>密钥只提交给本地后端进程，不进入浏览器存储、数据库配置历史或Excel。</p>
      </el-card>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { performanceApi } from "@/api/performance";
import { workbenchApi } from "@/api/workbench";
import StatusTag from "@/components/common/StatusTag.vue";

const screeningFields = [
  { key: "quant_top_n", label: "Quant入选数量", min: 1, max: 5000 },
  { key: "llm_analysis_n", label: "Flash分析数量", min: 1, max: 5000 },
  { key: "llm_top_n", label: "最终模型入选", min: 1, max: 5000 },
  { key: "final_display_n", label: "最终展示数量", min: 1, max: 5000 },
  { key: "manual_soft_limit", label: "人工池软提示", min: 1, max: 100 },
  { key: "manual_max_limit", label: "人工池最大数量", min: 1, max: 100 }
];
const workbench = reactive<Record<string, number>>({});
const performance = reactive<Record<string, string | number | boolean>>({});
const providers = ["tushare", "deepseek", "openai"];
const secretInput = reactive<Record<string, string>>({ tushare: "", deepseek: "", openai: "" });
const secretStatus = ref<Record<string, { configured: boolean }>>({});

async function load() {
  const [settings, performanceSettings] = await Promise.all([workbenchApi.settings(), performanceApi.settings()]);
  Object.assign(workbench, settings.data); Object.assign(performance, performanceSettings.data);
  secretStatus.value = window.aiTraderShell ? await window.aiTraderShell.secrets.status() : (await workbenchApi.secretStatus()).data;
}
async function saveAll() {
  try { await Promise.all([workbenchApi.updateSettings(workbench), performanceApi.updateSettings(performance)]); ElMessage.success("设置已保存并写入配置历史"); }
  catch { ElMessage.error("设置校验失败"); }
}
async function saveSecret(provider: string) {
  const value = secretInput[provider]; if (!value) return;
  if (window.aiTraderShell) await window.aiTraderShell.secrets.set(provider, value); else await workbenchApi.setSecret(provider, value);
  secretInput[provider] = ""; await load(); ElMessage.success("密钥已加密保存");
}
async function testSecret(provider: string) {
  const response = window.aiTraderShell ? await window.aiTraderShell.secrets.test(provider) : await workbenchApi.testSecret(provider);
  const payload = response as { data?: { status?: string } };
  ElMessage.info(payload.data?.status || "测试完成");
}
async function deleteSecret(provider: string) {
  await ElMessageBox.confirm("删除后相关真实任务将不可用，是否继续？", "删除密钥", { type: "warning" });
  if (window.aiTraderShell) await window.aiTraderShell.secrets.delete(provider); else await workbenchApi.deleteSecret(provider);
  await load(); ElMessage.success("密钥已删除");
}
onMounted(() => void load());
</script>

<style scoped>
.page-title { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }.page-title h1 { margin:0; font-size:22px; }
.settings-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }.secrets { grid-column:1 / -1; }
.secret-row { display:grid; grid-template-columns:80px 80px minmax(200px,1fr) auto auto auto; gap:8px; align-items:center; margin-bottom:10px; }
.secrets p { color:#667085; font-size:12px; text-align:center; }
</style>
