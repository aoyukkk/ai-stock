<template>
  <main class="wizard-shell">
    <header>
      <div class="brand">AI</div>
      <div><h1>AI Trader Assistant</h1><p>首次设置</p></div>
    </header>
    <el-steps :active="step" finish-status="success" align-center>
      <el-step v-for="title in titles" :key="title" :title="title" />
    </el-steps>

    <section class="step-content">
      <template v-if="step === 0"><h2>欢迎使用 1.0.0</h2><p>这是交易员日度筛选工作台。内置历史数据可以离线查看，真实任务由你明确启动。</p></template>
      <template v-else-if="step === 1"><h2>用户数据目录</h2><p>数据库、缓存、输出、日志和加密配置保存在以下目录：</p><code>{{ runtime.userData || "正在读取…" }}</code><p>升级安装不会覆盖这里已有的数据。</p></template>
      <template v-else-if="step === 2"><h2>Tushare Token</h2><p>用于真实行情更新和全 A 量化，可暂时跳过。</p><div class="secret"><el-input v-model="tushare" type="password" show-password placeholder="输入 Token" /><el-button type="primary" :disabled="!tushare" @click="saveSecret('tushare', tushare)">加密保存</el-button><StatusTag :status="secrets.tushare?.configured ? 'READY' : 'NOT_RUN'" /></div></template>
      <template v-else-if="step === 3"><h2>DeepSeek API Key</h2><p>用于用户确认预算后的 Flash 与 Pro 分析，可暂时跳过。</p><div class="secret"><el-input v-model="deepseek" type="password" show-password placeholder="输入 API Key" /><el-button type="primary" :disabled="!deepseek" @click="saveSecret('deepseek', deepseek)">加密保存</el-button><StatusTag :status="secrets.deepseek?.configured ? 'READY' : 'NOT_RUN'" /></div></template>
      <template v-else-if="step === 4"><h2>基础配置</h2><el-form label-width="170px" class="config-form"><el-form-item label="Quant 入选数量"><el-input-number v-model="settings.quant_top_n" :min="1" /></el-form-item><el-form-item label="LLM 分析数量"><el-input-number v-model="settings.llm_analysis_n" :min="1" /></el-form-item><el-form-item label="LLM 入选数量"><el-input-number v-model="settings.llm_top_n" :min="1" /></el-form-item><el-form-item label="验证账户权益"><el-input-number v-model="settings.validation_account_equity" :min="0" :step="10000" /></el-form-item><el-form-item label="可用现金"><el-input-number v-model="settings.validation_available_cash" :min="0" :step="10000" /></el-form-item></el-form></template>
      <template v-else-if="step === 5"><h2>内置历史数据</h2><div class="history"><span>Seed 版本</span><strong>{{ runtime.firstRun.seedVersion || "1.0.0" }}</strong><span>可用日期</span><strong>{{ dates.length }}</strong><span>最新交易日</span><strong>{{ dates[0]?.trade_date || "暂无" }}</strong><span>运行状态</span><strong>{{ dates[0]?.pipeline_status || "暂无" }}</strong></div></template>
      <template v-else><h2>设置完成</h2><p>未配置密钥也可以查看历史结果；真实数据和 LLM 任务会保持不可用，不会自动切换为模拟结果。</p></template>
    </section>

    <footer><el-button :disabled="step === 0" @click="step--">上一步</el-button><el-button v-if="step < 6" type="primary" @click="next">下一步</el-button><el-button v-else type="primary" @click="finish">进入工作台</el-button></footer>
  </main>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";

import { workbenchApi } from "@/api/workbench";
import StatusTag from "@/components/common/StatusTag.vue";
import type { AvailableTradeDate } from "@/types/workbench";

const router = useRouter();
const step = ref(0);
const titles = ["欢迎", "数据目录", "Tushare", "DeepSeek", "基础配置", "历史数据", "完成"];
const runtime = reactive({ userData: "", firstRun: { firstRun: true, seedVersion: null as string | null } });
const settings = reactive<Record<string, number>>({ quant_top_n: 500, llm_analysis_n: 100, llm_top_n: 20, validation_account_equity: 1000000, validation_available_cash: 1000000 });
const secrets = reactive<Record<string, { configured: boolean }>>({});
const dates = ref<AvailableTradeDate[]>([]);
const tushare = ref("");
const deepseek = ref("");

async function load() {
  if (window.aiTraderShell) Object.assign(runtime, await window.aiTraderShell.getRuntimeStatus());
  const [settingResponse, dateResponse] = await Promise.all([workbenchApi.settings(), workbenchApi.availableDates()]);
  Object.assign(settings, settingResponse.data);
  dates.value = dateResponse.data.items;
  Object.assign(secrets, window.aiTraderShell ? await window.aiTraderShell.secrets.status() : (await workbenchApi.secretStatus()).data);
}
async function saveSecret(provider: string, value: string) {
  if (!window.aiTraderShell) return;
  await window.aiTraderShell.secrets.set(provider, value);
  Object.assign(secrets, await window.aiTraderShell.secrets.status());
  if (provider === "tushare") tushare.value = ""; else deepseek.value = "";
  ElMessage.success("密钥已加密保存");
}
async function next() {
  if (step.value === 4) await workbenchApi.updateSettings(settings);
  step.value += 1;
}
function finish() { void router.replace("/workbench"); }
onMounted(() => void load());
</script>

<style scoped>
.wizard-shell { min-height:100vh; max-width:1040px; margin:0 auto; padding:36px 48px; box-sizing:border-box; display:grid; grid-template-rows:auto auto 1fr auto; gap:30px; color:#172033; }
header { display:flex; align-items:center; gap:14px; } header h1 { margin:0; font-size:25px; } header p { margin:3px 0 0; color:#667085; }
.brand { width:48px; height:48px; display:grid; place-items:center; background:#172033; color:white; font-weight:700; font-size:20px; border-radius:8px; }
.step-content { min-height:340px; padding:42px 6%; border-top:1px solid #e4e7ed; border-bottom:1px solid #e4e7ed; } h2 { font-size:24px; margin:0 0 18px; } p { color:#596579; line-height:1.8; } code { display:block; padding:14px; background:#f4f6f8; overflow-wrap:anywhere; }
.secret { display:grid; grid-template-columns:minmax(320px,1fr) auto 90px; gap:10px; align-items:center; max-width:760px; }.config-form { max-width:520px; }.history { display:grid; grid-template-columns:150px 1fr; gap:14px; max-width:520px; }.history span { color:#667085; }.history strong { text-align:left; }
footer { display:flex; justify-content:flex-end; gap:10px; }
@media (max-width:760px) { .wizard-shell { padding:22px 18px; }.secret { grid-template-columns:1fr; }.step-content { padding:28px 0; } }
</style>
