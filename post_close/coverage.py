from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.core.config import get_app_config
from database.models import MarketMinuteBarShadow, MarketSnapshotShadow, QuantRankResult, QuantRun
from post_close.scoring import IFindDataQuality, IFindEodEnhancementEngine, MinutePoint
from stock_codes import normalize_ts_code


SHANGHAI = ZoneInfo("Asia/Shanghai")
PRIORITY = {"HUMAN_HELD": 0, "AI_HELD": 1, "ACTIVE_ORDER_PLAN": 2, "MANUAL": 3, "FINAL": 4}


class IFindTieredCoverageService:
    def __init__(self, session, app_config=None) -> None:
        self.session = session
        self.app_config = app_config or get_app_config()
        values = self.app_config.config_files.get("post_close_action", {})
        self.enhancement_config = values.get("ifind_eod_enhancement", {})
        self.coverage_config = values.get("tiered_ifind_coverage", {})
        self.engine = IFindEodEnhancementEngine(self.enhancement_config)

    def evaluate(self, trade_date: date, pool: dict[str, Any], quant_run: QuantRun | None) -> dict[str, Any]:
        limit = max(0, int(self.coverage_config.get("max_full_minute_stocks", 20)))
        ordered = sorted(pool["items"], key=lambda item: (min((PRIORITY.get(origin, 99) for origin in item["origins"]), default=99), item["stock_code"]))
        requested_items = ordered[:limit]
        requested = [item["stock_code"] for item in requested_items]
        skipped = [item["stock_code"] for item in ordered[limit:]]
        start = datetime.combine(trade_date, time.min, tzinfo=SHANGHAI)
        end = start + timedelta(days=1)
        minute_rows = self.session.scalars(select(MarketMinuteBarShadow).where(
            MarketMinuteBarShadow.stock_code.in_(requested),
            MarketMinuteBarShadow.bar_time >= start,
            MarketMinuteBarShadow.bar_time < end,
            MarketMinuteBarShadow.provider == "IFIND_HTTP",
        ).order_by(MarketMinuteBarShadow.stock_code, MarketMinuteBarShadow.bar_time)).all() if requested else []
        grouped: dict[str, list[MarketMinuteBarShadow]] = {}
        for row in minute_rows:
            grouped.setdefault(normalize_ts_code(row.stock_code), []).append(row)
        snapshot_rows = self.session.scalars(select(MarketSnapshotShadow).where(
            MarketSnapshotShadow.stock_code.in_(requested),
            MarketSnapshotShadow.snapshot_time >= start,
            MarketSnapshotShadow.snapshot_time < end,
            MarketSnapshotShadow.provider == "IFIND_HTTP",
        ).order_by(MarketSnapshotShadow.snapshot_time.desc())).all() if requested else []
        snapshots: dict[str, MarketSnapshotShadow] = {}
        for row in snapshot_rows:
            snapshots.setdefault(normalize_ts_code(row.stock_code), row)
        snapshot_codes = set(snapshots)
        full_codes = [code for code in requested if code in snapshot_codes and len(grouped.get(code, [])) >= 30]
        partial_only = [code for code in requested if code in snapshot_codes and code not in full_codes]
        missing = [code for code in requested if code not in snapshot_codes]
        scores = self._full_scores(full_codes, grouped, quant_run)
        ranking = sorted(scores, key=lambda item: (-item["full_enhanced_score"], item["base_rank"], item["stock_code"]))
        for rank, item in enumerate(ranking, start=1):
            item["full_action_pool_rank"] = rank
        deltas = [item["overlay_delta"] for item in ranking]
        coverage_items = []
        for code in requested:
            rows = grouped.get(code, [])
            snapshot = snapshots.get(code)
            feature_scope = "FULL_MINUTE_OVERLAY" if code in full_codes else "PARTIAL_SNAPSHOT_ONLY" if snapshot else "BASELINE_ONLY"
            coverage_items.append({
                "stock_code": code,
                "feature_scope": feature_scope,
                "snapshot_covered": snapshot is not None,
                "minute_covered": code in full_codes,
                "minute_bar_count": len(rows),
                "minute_completeness": min(1.0, len(rows) / 30),
                "latest_minute_time": rows[-1].bar_time if rows else None,
                "provider_time": snapshot.provider_time if snapshot else None,
                "freshness": snapshot.data_status if snapshot else "NOT_AVAILABLE",
                "dual_source_status": "NOT_EVALUATED",
            })
        return {
            "feature_scope": "FULL_MINUTE_OVERLAY",
            "requested_minute_stocks": len(requested), "returned_minute_stocks": len(grouped),
            "full_overlay_count": len(full_codes), "partial_only_count": len(partial_only),
            "skipped_by_quota": len(skipped), "skipped_by_priority": 0,
            "missing_count": len(missing),
            "coverage_ratio": len(full_codes) / len(requested) if requested else 0.0,
            "external_calls": 0, "cache_hits": len(grouped),
            "priority_order": [item["stock_code"] for item in requested_items],
            "ranking_scope": "ACTION_POOL_FULL_COVERAGE_ONLY",
            "ab_comparability": "COMPARABLE" if full_codes and len(full_codes) == len(requested) else "NOT_COMPARABLE_COVERAGE_MISMATCH",
            "overlay_distribution": {"count": len(deltas), "minimum": min(deltas) if deltas else None, "median": median(deltas) if deltas else None, "maximum": max(deltas) if deltas else None},
            "ranking": ranking,
            "items": coverage_items,
        }

    def _full_scores(self, codes: list[str], grouped: dict[str, list[MarketMinuteBarShadow]], quant_run: QuantRun | None) -> list[dict[str, Any]]:
        if not quant_run or not codes:
            return []
        quant = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(QuantRankResult).where(
            QuantRankResult.quant_run_id == quant_run.run_id,
            QuantRankResult.stock_code.in_(codes),
        ))}
        result = []
        for code in codes:
            base = quant.get(code)
            if not base: continue
            rows = grouped[code][-30:]
            points = [MinutePoint(float(row.close), float(row.high), float(row.low), float(row.volume), float(row.amount)) for row in rows]
            features = self.engine.features_from_minutes(points, stock_return=0.0, index_return=0.0, expected_count=30)
            quality = IFindDataQuality(1, 1, min(1, len(rows) / 30), 1, 1)
            score = self.engine.score(float(base.total_score), features, quality, minute_available=True)
            result.append({"stock_code": code, "base_rank": base.rank, "base_score": float(base.total_score), "full_enhanced_score": score.enhanced_score, "overlay_delta": score.overlay_delta, "component_scores": score.component_scores})
        return result
