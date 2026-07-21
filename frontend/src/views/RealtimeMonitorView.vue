<template>
  <section class="monitor-page">
    <header class="page-head">
      <div>
        <h1>实时盯盘与提醒</h1>
        <p>仅监测交易员确认的股票；规则提醒可确认、静音和追溯，不执行任何交易动作。</p>
      </div>
      <div class="command-bar">
        <el-button v-if="!session" type="primary" @click="createSession">创建今日 Session</el-button>
        <template v-else>
          <el-button v-if="['DRAFT', 'READY', 'INTERRUPTED'].includes(session.status)" type="primary" :disabled="!pool.length" @click="transition('start')">启动盯盘</el-button>
          <el-button v-if="session.status.startsWith('ACTIVE')" @click="transition('pause')">暂停</el-button>
          <el-button v-if="['PAUSED_USER', 'PAUSED_DATA_SOURCE', 'INTERRUPTED'].includes(session.status)" type="primary" @click="transition('resume')">恢复</el-button>
          <el-button :icon="Refresh" :loading="loading" @click="refreshNow">立即刷新</el-button>
          <el-button type="danger" plain :disabled="session.status === 'POST_MARKET_STOPPED'" @click="transition('stop')">停止</el-button>
        </template>
      </div>
    </header>

    <el-alert type="success" show-icon :closable="false" title="观察辅助模式：真实交易关闭；不自动下单，不执行买入、卖出、撤单或改价；刷新循环不调用 LLM。" />
    <el-alert v-if="session?.status === 'PAUSED_MIDDAY'" class="notice" type="warning" show-icon :closable="false" title="午间资源保护中：盯盘已暂停，午盘推荐拥有 P0 优先级；现有盯盘池和提醒均已保留。" />

    <div class="status-strip">
      <div><span>交易日</span><strong>{{ tradeDate }}</strong></div>
      <div><span>市场时段</span><strong>{{ session?.market_session || '-' }}</strong></div>
      <div><span>Session</span><strong>{{ session?.status || '尚未创建' }}</strong></div>
      <div><span>盯盘股票</span><strong>{{ pool.length }} / 20</strong></div>
      <div><span>最近刷新</span><strong>{{ shortTime(session?.updated_at) }}</strong></div>
      <div><span>外部调用</span><strong>{{ session?.external_call_count || 0 }}</strong></div>
      <div><span>缓存命中</span><strong>{{ session?.cache_hit_count || 0 }}</strong></div>
      <div><span>未确认</span><strong>{{ unread.unread }}</strong></div>
      <div><span>WARNING</span><strong>{{ unread.warning }}</strong></div>
      <div><span>CRITICAL</span><strong>{{ unread.critical }}</strong></div>
      <div><span>午盘资源</span><strong>{{ session?.status === 'PAUSED_MIDDAY' ? 'P0 已保护' : '待命' }}</strong></div>
    </div>

    <el-tabs v-model="activeTab" class="workspace-tabs">
      <el-tab-pane label="主监测" name="monitor">
        <section class="tool-band">
          <div class="section-head"><div><h2>盯盘池</h2><p>每次加入都需要确认；午盘推荐不会自动替换现有池。</p></div>
            <div class="command-bar">
              <el-button :disabled="!session" @click="openDirectAdd">搜索/批量添加</el-button>
              <el-button :disabled="!session" @click="openMidday">从午盘推荐选择</el-button>
            </div>
          </div>
          <CenteredDataTable :rows="poolRows" :columns="poolColumns" :pagination-enabled="false" height="220">
            <template #actions="{ row }">
              <div class="row-actions"><el-button size="small" @click="togglePause(row)">{{ row.paused ? '恢复' : '暂停' }}</el-button><el-button size="small" type="danger" plain @click="removeItem(row)">移除</el-button></div>
            </template>
          </CenteredDataTable>
        </section>

        <section class="tool-band">
          <div class="section-head"><div><h2>实时监测</h2><p>快照按优先级分层刷新；分钟数据仅对少量高优先级股票按需获取。</p></div></div>
          <CenteredDataTable :rows="resultRows" :columns="resultColumns" :pagination-enabled="false" height="430" @row-double-click="openDetail">
            <template #actions="{ row }"><el-button size="small" @click="openDetail(row)">详情</el-button></template>
          </CenteredDataTable>
        </section>
      </el-tab-pane>

      <el-tab-pane :label="`提醒中心 (${unread.unread})`" name="alerts">
        <section class="tool-band">
          <div class="section-head"><div><h2>提醒中心</h2><p>同一规则持续命中只累计次数；交易员操作均保留审计记录。</p></div></div>
          <CenteredDataTable :rows="alertRows" :columns="alertColumns" :total="alertTotal" :current-page="alertPage" :page-size="50" height="560" @pagination-change="changeAlertPage">
            <template #actions="{ row }">
              <div class="row-actions">
                <el-button size="small" @click="alertAction(row, 'acknowledge')">确认</el-button>
                <el-button size="small" @click="alertAction(row, 'mute')">静音15分钟</el-button>
                <el-button size="small" type="success" plain @click="alertAction(row, 'resolve')">解决</el-button>
                <el-button size="small" @click="explain(row)">AI解释</el-button>
              </div>
            </template>
          </CenteredDataTable>
        </section>
      </el-tab-pane>
      <el-tab-pane label="规则与审计" name="audit">
        <section class="tool-band">
          <div class="section-head"><div><h2>提醒规则</h2><p>规则来自所选模板和交易员配置；刷新循环只执行确定性判断。</p></div></div>
          <CenteredDataTable :rows="ruleRows" :columns="ruleColumns" :pagination-enabled="false" height="260" />
        </section>
        <section class="tool-band">
          <div class="section-head"><div><h2>Session 历史</h2><p>每日 Session 独立保存，不会在应用启动时自动恢复。</p></div></div>
          <CenteredDataTable :rows="historyRows" :columns="historyColumns" :pagination-enabled="false" height="260" />
        </section>
        <section class="tool-band">
          <div class="section-head"><div><h2>调用审计</h2><p>仅保存请求计数、延迟和结果状态，不保存令牌、请求头或完整响应。</p></div></div>
          <CenteredDataTable :rows="usageRows" :columns="usageColumns" :pagination-enabled="false" height="300" />
        </section>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="addDialog" title="添加盯盘股票" width="620px">
      <el-form label-width="96px">
        <el-form-item label="股票代码"><el-input v-model="directCodes" type="textarea" :rows="4" placeholder="每行一个代码，例如 000001.SZ" /></el-form-item>
        <el-form-item label="监测模板"><el-select v-model="draftProfile"><el-option v-for="item in profiles" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <el-form-item label="优先级"><el-select v-model="draftPriority"><el-option v-for="item in priorities" :key="item" :label="item" :value="item" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="addDialog = false">取消</el-button><el-button type="primary" @click="confirmDirectAdd">预览并确认</el-button></template>
    </el-dialog>

    <el-dialog v-model="middayDialog" title="选择午盘推荐加入下午盯盘池" width="900px">
      <el-alert type="info" :closable="false" title="勾选后仍需二次确认；不会自动清空或替换上午盯盘池。" />
      <CenteredDataTable class="dialog-table" :rows="middayRows" :columns="middayColumns" selectable :pagination-enabled="false" height="380" @selection-change="handleMiddaySelection" />
      <template #footer><el-button @click="middayDialog = false">取消</el-button><el-button type="primary" :disabled="!middaySelected.length" @click="confirmMiddayAdd">确认加入 {{ middaySelected.length }} 只</el-button></template>
    </el-dialog>

    <el-drawer v-model="detailDrawer" title="个股监测详情" size="640px">
      <div v-if="selectedStock" class="detail-grid">
        <div><span>股票</span><strong>{{ selectedStock.stock_code }} {{ selectedStock.stock_name_snapshot }}</strong></div>
        <div><span>最新价</span><strong>{{ price(selectedStock.latest) }}</strong></div>
        <div><span>数据时间</span><strong>{{ shortTime(selectedStock.provider_time) }}</strong></div>
        <div><span>数据质量</span><strong>{{ selectedStock.data_status || '-' }}</strong></div>
        <div><span>当前提醒</span><strong>{{ selectedStock.current_alert || '无' }}</strong></div>
        <div><span>行业/概念实时</span><strong>暂不可用</strong></div>
      </div>
      <el-alert type="info" :closable="false" title="分钟行情可能延迟约 1–3 分钟，仅用于结构确认，不用于秒级止损。" />
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import CenteredDataTable from "@/components/common/CenteredDataTable.vue";
import { actOnMonitorAlert, confirmMonitorPool, createMonitorSession, explainMonitorAlert, getCurrentMonitorSession, getMiddayMonitorSuggestions, getMonitorAlerts, getMonitorRules, getMonitorSessionHistory, getMonitorUnreadCount, getMonitorUsage, getSelectedMonitorPool, getSelectedMonitorResults, previewMonitorPool, refreshSelectedMonitor, removeMonitorItem, transitionMonitorSession, updateMonitorItem } from "@/api/realtime";
import { useWorkbenchStore } from "@/stores/workbench";
import type { MonitorAlert, MonitorPoolCandidate, MonitorSession, SelectedMonitorItem } from "@/types/realtime";
import type { TableColumn } from "@/types/workbench";

const store = useWorkbenchStore();
const tradeDate = computed(() => store.tradeDate || new Date().toISOString().slice(0, 10));
const session = ref<MonitorSession | null>(null);
const pool = ref<SelectedMonitorItem[]>([]);
const results = ref<SelectedMonitorItem[]>([]);
const alerts = ref<MonitorAlert[]>([]);
const rules = ref<Record<string, unknown>[]>([]);
const history = ref<Record<string, unknown>[]>([]);
const usage = ref<Record<string, unknown>[]>([]);
const unread = ref({ unread: 0, warning: 0, critical: 0 });
const loading = ref(false);
const activeTab = ref("monitor");
const addDialog = ref(false);
const middayDialog = ref(false);
const detailDrawer = ref(false);
const directCodes = ref("");
const draftProfile = ref("CANDIDATE_MONITOR");
const draftPriority = ref("NORMAL");
const middayRows = ref<MonitorPoolCandidate[]>([]);
const middaySelected = ref<MonitorPoolCandidate[]>([]);
const middayRunId = ref<string | null>(null);
const selectedStock = ref<SelectedMonitorItem | null>(null);
const alertPage = ref(1);
const alertTotal = ref(0);
let timer: number | undefined;
let removeOpenListener: (() => void) | undefined;
const notified = new Set<number>();

const profiles = [
  { value: "CANDIDATE_MONITOR", label: "候选股监测" },
  { value: "POSITION_RISK_MONITOR", label: "持仓风险监测" },
  { value: "ORDER_PLAN_MONITOR", label: "挂单计划监测" },
  { value: "CUSTOM_MONITOR", label: "自定义规则" }
];
const priorities = ["CRITICAL", "HIGH", "NORMAL", "LOW"];
const poolRows = computed(() => pool.value as unknown as Record<string, unknown>[]);
const resultRows = computed(() => results.value as unknown as Record<string, unknown>[]);
const alertRows = computed(() => alerts.value as unknown as Record<string, unknown>[]);
const ruleRows = computed(() => rules.value);
const historyRows = computed(() => history.value);
const usageRows = computed(() => usage.value);

const poolColumns: TableColumn[] = [
  { key: "stock_code", label: "股票代码" }, { key: "stock_name_snapshot", label: "股票名称" },
  { key: "source_json", label: "来源", minWidth: 160 }, { key: "monitor_profile", label: "模板", minWidth: 180 },
  { key: "priority", label: "优先级" }, { key: "paused", label: "状态", formatter: (v) => v ? "已暂停" : "监测中" },
  { key: "actions", label: "操作", minWidth: 170 }
];
const resultColumns: TableColumn[] = [
  { key: "stock_code", label: "股票代码" }, { key: "stock_name_snapshot", label: "股票名称" },
  { key: "source_json", label: "来源", minWidth: 150 }, { key: "monitor_profile", label: "模板", minWidth: 170 },
  { key: "priority", label: "优先级" }, { key: "latest", label: "最新价", formatter: price },
  { key: "change_percent", label: "涨跌幅", formatter: percent }, { key: "provider_time", label: "数据时间", minWidth: 150, formatter: shortTime },
  { key: "data_status", label: "新鲜度" }, { key: "current_alert_severity", label: "提醒等级" },
  { key: "current_alert", label: "当前提醒", minWidth: 240 }, { key: "last_alert_time", label: "最后提醒", minWidth: 150, formatter: shortTime },
  { key: "alert_status", label: "提醒状态" }, { key: "actions", label: "操作" }
];
const alertColumns: TableColumn[] = [
  { key: "triggered_at", label: "时间", minWidth: 150, formatter: shortTime }, { key: "stock_code", label: "股票" },
  { key: "severity", label: "级别" }, { key: "title", label: "规则", minWidth: 180 },
  { key: "current_value_json", label: "当前值", minWidth: 130 }, { key: "threshold_json", label: "阈值", minWidth: 150 },
  { key: "message", label: "说明", minWidth: 280 }, { key: "provider_time", label: "数据时间", minWidth: 150, formatter: shortTime },
  { key: "status", label: "状态" }, { key: "actions", label: "交易员操作", minWidth: 360 }
];
const middayColumns: TableColumn[] = [
  { key: "stock_code", label: "股票代码" }, { key: "stock_name", label: "股票名称" },
  { key: "source", label: "来源" }, { key: "monitor_profile", label: "模板", minWidth: 180 }, { key: "priority", label: "优先级" }
];
const ruleColumns: TableColumn[] = [
  { key: "stock_code", label: "股票代码" }, { key: "rule_type", label: "规则类型", minWidth: 190 },
  { key: "threshold_json", label: "阈值", minWidth: 170 }, { key: "comparison", label: "比较" },
  { key: "severity", label: "级别" }, { key: "cooldown_seconds", label: "冷却(秒)" },
  { key: "consecutive_hits_required", label: "连续命中" }, { key: "hysteresis_percent", label: "滞回(%)" }, { key: "enabled", label: "启用" }
];
const historyColumns: TableColumn[] = [
  { key: "trade_date", label: "交易日" }, { key: "status", label: "状态", minWidth: 170 },
  { key: "market_session", label: "市场时段" }, { key: "pool_version", label: "池版本" },
  { key: "stock_count", label: "股票数" }, { key: "external_call_count", label: "外部调用" },
  { key: "cache_hit_count", label: "缓存命中" }, { key: "alert_count", label: "提醒数" }, { key: "updated_at", label: "更新时间", minWidth: 170, formatter: shortTime }
];
const usageColumns: TableColumn[] = [
  { key: "started_at", label: "开始时间", minWidth: 170, formatter: shortTime }, { key: "refresh_type", label: "刷新类型" },
  { key: "priority", label: "优先级" }, { key: "requested_codes", label: "请求代码", minWidth: 180 },
  { key: "returned_codes", label: "返回代码", minWidth: 180 }, { key: "missing_codes", label: "缺失代码", minWidth: 160 },
  { key: "external_calls", label: "外部调用" }, { key: "cache_hits", label: "缓存命中" },
  { key: "latency_ms", label: "延迟(ms)" }, { key: "status", label: "状态" }, { key: "error_category", label: "错误类别", minWidth: 150 }
];

async function loadAll() {
  session.value = (await getCurrentMonitorSession(tradeDate.value)).data;
  if (!session.value) { pool.value = []; results.value = []; alerts.value = []; return; }
  const id = session.value.id;
  const [poolResponse, resultResponse, alertResponse, countResponse] = await Promise.all([
    getSelectedMonitorPool(id), getSelectedMonitorResults(id), getMonitorAlerts(id, alertPage.value), getMonitorUnreadCount(id)
  ]);
  pool.value = poolResponse.data.items;
  results.value = resultResponse.data.items;
  alerts.value = alertResponse.data.items;
  alertTotal.value = alertResponse.data.total;
  unread.value = countResponse.data;
  syncTimer();
  await loadAudit();
}
async function loadAudit() {
  const historyResponse = await getMonitorSessionHistory();
  history.value = historyResponse.data.items as unknown as Record<string, unknown>[];
  if (!session.value) { rules.value = []; usage.value = []; return; }
  const [ruleResponse, usageResponse] = await Promise.all([getMonitorRules(session.value.id), getMonitorUsage(session.value.id)]);
  rules.value = ruleResponse.data.items;
  usage.value = usageResponse.data.items;
}

async function createSession() { session.value = (await createMonitorSession(tradeDate.value)).data; ElMessage.success("今日 Session 已创建，请先选择盯盘股票。"); }
async function transition(action: "start" | "pause" | "resume" | "stop") {
  if (!session.value) return;
  if (action === "start") await ElMessageBox.confirm("确认启动仅针对当前盯盘池的实时监测？系统不会执行交易。", "启动盯盘", { type: "warning" });
  session.value = (await transitionMonitorSession(session.value.id, action)).data;
  syncTimer();
}
async function refreshNow() {
  if (!session.value || loading.value) return;
  loading.value = true;
  try {
    const response = await refreshSelectedMonitor(session.value.id);
    const freshAlerts = (response.data.alerts || []) as Array<{ id: number; title: string; message: string; severity: string; stock_code: string }>;
    for (const alert of freshAlerts) notify(alert);
    await loadAll();
  } finally { loading.value = false; }
}
function syncTimer() {
  if (timer) window.clearInterval(timer);
  timer = undefined;
  if (session.value?.status.startsWith("ACTIVE")) timer = window.setInterval(() => void refreshNow(), 20_000);
}
function openDirectAdd() { directCodes.value = ""; addDialog.value = true; }
async function confirmDirectAdd() {
  if (!session.value) return;
  const codes = [...new Set(directCodes.value.split(/[\s,;，；]+/).map((v) => v.trim()).filter(Boolean))];
  if (!codes.length) return ElMessage.warning("请输入至少一个股票代码。");
  const incoming = codes.map((stock_code) => ({ stock_code, source: "DIRECT_SEARCH", monitor_profile: draftProfile.value, priority: draftPriority.value }));
  await mergeAndConfirm(incoming, "DIRECT_SEARCH");
  addDialog.value = false;
}
async function openMidday() {
  const response = await getMiddayMonitorSuggestions(tradeDate.value);
  middayRows.value = response.data.items;
  middayRunId.value = response.data.run_id;
  middaySelected.value = [];
  middayDialog.value = true;
}
async function confirmMiddayAdd() { await mergeAndConfirm(middaySelected.value, "MIDDAY_RECOMMENDATION", middayRunId.value || undefined); middayDialog.value = false; }
async function mergeAndConfirm(incoming: MonitorPoolCandidate[], source: string, runId?: string) {
  if (!session.value) return;
  const existing: MonitorPoolCandidate[] = pool.value.map((item) => ({ stock_code: item.stock_code, stock_name: item.stock_name_snapshot, sources: item.source_json, monitor_profile: item.monitor_profile, priority: item.priority }));
  const byCode = new Map(existing.map((item) => [item.stock_code, item]));
  incoming.forEach((item) => byCode.set(item.stock_code, item));
  const merged = [...byCode.values()];
  const preview = (await previewMonitorPool(session.value.id, merged, source, runId)).data as { deduplicated_count?: number; added_count?: number; warning?: string };
  await ElMessageBox.confirm(`确认后的盯盘池共 ${preview.deduplicated_count || 0} 只，本次新增 ${preview.added_count || 0} 只。${preview.warning || ''}`, "确认盯盘池", { type: "warning" });
  await confirmMonitorPool(session.value.id, merged, source, runId);
  await loadAll();
}
async function togglePause(raw: Record<string, unknown>) { const row = raw as unknown as SelectedMonitorItem; await updateMonitorItem(row.id, { paused: !row.paused }); await loadAll(); }
async function removeItem(raw: Record<string, unknown>) { const row = raw as unknown as SelectedMonitorItem; await ElMessageBox.confirm(`确认从盯盘池移除 ${row.stock_code}？`, "移除股票", { type: "warning" }); await removeMonitorItem(row.id); await loadAll(); }
async function alertAction(raw: Record<string, unknown>, action: "acknowledge" | "mute" | "resolve") { const row = raw as unknown as MonitorAlert; await actOnMonitorAlert(row.id, action); await loadAll(); }
async function explain(raw: Record<string, unknown>) { const row = raw as unknown as MonitorAlert; const response = await explainMonitorAlert(row.id); ElMessage.info(`AI解释状态：${String(response.data.decision || response.data.status)}`); }
function changeAlertPage(payload: { page: number }) { alertPage.value = payload.page; void loadAll(); }
function handleMiddaySelection(rows: Record<string, unknown>[]) { middaySelected.value = rows as unknown as MonitorPoolCandidate[]; }
function openDetail(raw: Record<string, unknown>) { selectedStock.value = raw as unknown as SelectedMonitorItem; detailDrawer.value = true; }
function notify(alert: { id: number; title: string; message: string; severity: string; stock_code: string }) {
  if (notified.has(alert.id)) return;
  notified.add(alert.id);
  if (alert.severity === "WARNING" || alert.severity === "CRITICAL") void window.aiTraderShell?.notifyMonitorAlert({ title: alert.title, body: alert.message, severity: alert.severity, stockCode: alert.stock_code });
  ElMessage({ type: alert.severity === "CRITICAL" ? "error" : "warning", message: alert.title });
}
function shortTime(value: unknown): string { if (!value) return "-"; const parsed = new Date(String(value)); return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN", { hour12: false }); }
function price(value: unknown): string { return value === null || value === undefined ? "-" : Number(value).toFixed(2); }
function percent(value: unknown): string { return value === null || value === undefined ? "-" : `${Number(value).toFixed(2)}%`; }

onMounted(async () => {
  await loadAll();
  if (!session.value) await loadAudit();
  removeOpenListener = window.aiTraderShell?.onOpenMonitorStock((code) => { const row = results.value.find((item) => item.stock_code === code); if (row) openDetail(row as unknown as Record<string, unknown>); });
});
onBeforeUnmount(() => { if (timer) window.clearInterval(timer); removeOpenListener?.(); });
</script>

<style scoped>
.monitor-page { display: grid; gap: 14px; }
.page-head, .section-head, .command-bar, .row-actions { display: flex; align-items: center; }
.page-head, .section-head { justify-content: space-between; gap: 16px; }
.page-head h1 { margin: 0 0 4px; font-size: 26px; color: #17365d; }
.page-head p, .section-head p { margin: 0; color: #66788a; }
.command-bar, .row-actions { gap: 8px; justify-content: center; flex-wrap: wrap; }
.notice { margin-top: -4px; }
.status-strip { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); border: 1px solid #dfe5ec; background: #fff; }
.status-strip div { min-height: 66px; padding: 10px; display: grid; place-items: center; border-right: 1px solid #e6ebf0; border-bottom: 1px solid #e6ebf0; text-align: center; }
.status-strip span, .detail-grid span { color: #66788a; font-size: 12px; }
.status-strip strong { color: #172333; font-size: 15px; }
.workspace-tabs, .tool-band { background: #fff; }
.workspace-tabs { padding: 0 14px 14px; border: 1px solid #dfe5ec; }
.tool-band { padding: 12px 0 18px; }
.section-head { margin-bottom: 10px; }
.section-head h2 { margin: 0 0 3px; font-size: 17px; color: #17365d; }
.dialog-table { margin-top: 12px; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
.detail-grid div { min-height: 72px; display: grid; place-items: center; padding: 10px; border: 1px solid #dfe5ec; text-align: center; }
@media (max-width: 1200px) { .status-strip { grid-template-columns: repeat(4, minmax(120px, 1fr)); } }
@media (max-width: 760px) {
  .page-head, .section-head { align-items: flex-start; flex-direction: column; }
  .page-head h1 { font-size: 21px; }
  .command-bar { justify-content: flex-start; }
  .status-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .status-strip div { min-height: 58px; padding: 8px 5px; }
  .workspace-tabs { padding: 0 8px 10px; }
  .tool-band { padding-top: 9px; }
  .detail-grid { grid-template-columns: 1fr; }
  .row-actions { min-width: 210px; }
}
</style>
