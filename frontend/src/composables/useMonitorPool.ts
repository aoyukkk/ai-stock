import { ElMessage, ElMessageBox } from "element-plus";

import { confirmMonitorPool, createMonitorSession, getCurrentMonitorSession, getSelectedMonitorPool, previewMonitorPool } from "@/api/realtime";
import type { MonitorPoolCandidate } from "@/types/realtime";

export function useMonitorPool() {
  async function addToMonitor(tradeDate: string, incoming: MonitorPoolCandidate[], source: string, sourceRunId?: string) {
    if (!incoming.length) return ElMessage.warning("请先勾选要加入盯盘的股票。");
    let session = (await getCurrentMonitorSession(tradeDate)).data;
    if (!session) {
      await ElMessageBox.confirm("今日尚未创建盯盘 Session。确认创建 DRAFT Session 并预览所选股票？", "创建盯盘 Session", { type: "warning" });
      session = (await createMonitorSession(tradeDate)).data;
    }
    const current = (await getSelectedMonitorPool(session.id, 1, 50)).data.items;
    const merged = new Map<string, MonitorPoolCandidate>();
    current.forEach((item) => merged.set(item.stock_code, {
      stock_code: item.stock_code, stock_name: item.stock_name_snapshot, sources: item.source_json,
      monitor_profile: item.monitor_profile, priority: item.priority
    }));
    incoming.forEach((item) => merged.set(item.stock_code, item));
    const items = [...merged.values()];
    const preview = (await previewMonitorPool(session.id, items, source, sourceRunId)).data as { deduplicated_count?: number; added_count?: number; warning?: string };
    await ElMessageBox.confirm(`确认后的盯盘池共 ${preview.deduplicated_count || 0} 只，本次新增 ${preview.added_count || 0} 只。${preview.warning || ""}`, "确认加入盯盘", { type: "warning" });
    await confirmMonitorPool(session.id, items, source, sourceRunId);
    ElMessage.success("已生成新的盯盘池版本；不会自动启动监测。");
  }
  return { addToMonitor };
}
