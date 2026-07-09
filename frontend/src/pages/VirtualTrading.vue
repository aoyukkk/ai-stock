<template>
  <PageHeader :title="language.t('nav.virtualTrading')">
    <el-button :icon="Plus" type="primary" plain :disabled="app.realTradingEnabled" @click="createAccount">
      {{ language.t("virtual.createAccount") }}
    </el-button>
    <el-button :icon="Refresh" type="primary" :loading="loading" :disabled="app.realTradingEnabled" @click="runVirtual">
      {{ language.t("virtual.runPlans") }}
    </el-button>
  </PageHeader>

  <section v-if="app.realTradingEnabled" class="page-section">
    <el-alert type="error" :closable="false" show-icon :title="language.t('virtual.alertUnsafe')" />
  </section>

  <section class="page-section">
    <el-alert type="success" :closable="false" show-icon :title="language.t('virtual.alertSafe')" />
  </section>

  <section class="page-section content-grid">
    <StatusCard :label="language.t('virtual.initialCash')" :value="String(configValue('paper_trading.initial_cash'))" />
    <StatusCard :label="language.t('virtual.commissionRate')" :value="String(configValue('paper_trading.cost.commission_rate'))" />
    <StatusCard :label="language.t('virtual.stampTaxRate')" :value="String(configValue('paper_trading.cost.stamp_tax_rate'))" />
    <StatusCard :label="language.t('virtual.slippageRate')" :value="String(configValue('paper_trading.cost.slippage_rate'))" />
    <StatusCard :label="language.t('virtual.allowPartialFill')" :value="String(configValue('paper_trading.rules.allow_partial_fill'))" />
    <StatusCard label="real_trading_enabled" :value="String(config.effective?.real_trading_enabled ?? false)" />
  </section>

  <section class="page-section content-grid">
    <StatusCard :label="language.t('virtual.cash')" :value="String(account.cash ?? '-')" />
    <StatusCard :label="language.t('virtual.totalAsset')" :value="String(account.total_asset ?? '-')" />
    <StatusCard :label="language.t('dailyReview.profitLoss')" :value="String(account.profit_loss ?? '-')" />
  </section>

  <section v-loading="loading || refreshing" class="page-section table-wrap">
    <el-tabs>
      <el-tab-pane :label="language.t('virtual.orders')">
        <el-table :data="orders" border>
          <el-table-column prop="order_id" :label="language.t('common.id')" width="80" />
          <el-table-column prop="stock_code" :label="language.t('common.code')" width="110" />
          <el-table-column prop="action" :label="language.t('common.actions')" width="100" />
          <el-table-column prop="order_price" :label="language.t('common.price')" width="110" />
          <el-table-column prop="order_quantity" :label="language.t('common.qty')" width="100" />
          <el-table-column prop="status" :label="language.t('common.status')" width="130" />
          <el-table-column :label="language.t('virtual.actions')" width="260">
            <template #default="{ row }">
              <el-button size="small" :icon="Close" :disabled="app.realTradingEnabled" @click="cancelVirtual(row.order_id)">{{ language.t("virtual.cancelOrder") }}</el-button>
              <el-button size="small" :icon="Edit" :disabled="app.realTradingEnabled" @click="repriceVirtual(row.order_id, Number(row.order_price || 0))">{{ language.t("virtual.repriceOrder") }}</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty :description="language.t('virtual.noOrders')" />
          </template>
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="language.t('virtual.positions')">
        <el-table :data="positions" border>
          <el-table-column prop="stock_code" :label="language.t('common.code')" />
          <el-table-column prop="quantity" :label="language.t('common.qty')" />
          <el-table-column prop="latest_price" :label="language.t('virtual.latest')" />
          <el-table-column prop="market_value" :label="language.t('virtual.marketValue')" />
          <template #empty>
            <el-empty :description="language.t('virtual.noPositions')" />
          </template>
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="language.t('virtual.trades')">
        <el-table :data="trades" border>
          <el-table-column prop="trade_id" :label="language.t('common.id')" />
          <el-table-column prop="stock_code" :label="language.t('common.code')" />
          <el-table-column prop="action" :label="language.t('common.actions')" />
          <el-table-column prop="price" :label="language.t('common.price')" />
          <el-table-column prop="amount" :label="language.t('common.amount')" />
          <template #empty>
            <el-empty :description="language.t('virtual.noTrades')" />
          </template>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup lang="ts">
import { Close, Edit, Plus, Refresh } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { onMounted, ref } from "vue";

import { cancelOrder, createDefaultAccount, getAccount, getOrders, getPositions, getTrades, repriceOrder, runPlans } from "@/api/virtualTrading";
import PageHeader from "@/components/PageHeader.vue";
import StatusCard from "@/components/StatusCard.vue";
import { useAppStore } from "@/stores/appStore";
import { useConfigStore } from "@/stores/configStore";
import { useLanguageStore } from "@/stores/languageStore";
import { rowsFrom } from "@/utils/data";

const app = useAppStore();
const config = useConfigStore();
const language = useLanguageStore();
const loading = ref(false);
const refreshing = ref(false);
const account = ref<Record<string, unknown>>({});
const orders = ref<Record<string, unknown>[]>([]);
const positions = ref<Record<string, unknown>[]>([]);
const trades = ref<Record<string, unknown>[]>([]);

async function refreshAll() {
  refreshing.value = true;
  try {
    const [accountResp, ordersResp, positionsResp, tradesResp] = await Promise.all([getAccount(), getOrders(), getPositions(), getTrades()]);
    account.value = accountResp.data;
    orders.value = rowsFrom(ordersResp.data, "orders");
    positions.value = rowsFrom(positionsResp.data, "positions");
    trades.value = rowsFrom(tradesResp.data, "trades");
  } finally {
    refreshing.value = false;
  }
}

async function createAccount() {
  account.value = (await createDefaultAccount()).data;
  await refreshAll();
}

async function runVirtual() {
  loading.value = true;
  try {
    await runPlans({ input_top_n: 5, persist_plans: false });
    await refreshAll();
  } finally {
    loading.value = false;
  }
}

async function cancelVirtual(orderId: number) {
  await cancelOrder(orderId, "frontend virtual cancel");
  ElMessage.success(language.t("virtual.cancelSuccess"));
  await refreshAll();
}

async function repriceVirtual(orderId: number, currentPrice: number) {
  await repriceOrder(orderId, Number((currentPrice * 0.99).toFixed(2)), "frontend virtual reprice");
  ElMessage.success(language.t("virtual.repriceSuccess"));
  await refreshAll();
}

function configValue(key: string) {
  return config.effective?.values[key] ?? "-";
}

onMounted(() => {
  void Promise.all([refreshAll(), config.loadConfig()]);
});
</script>
