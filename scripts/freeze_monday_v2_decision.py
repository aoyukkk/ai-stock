from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import stat
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.shadow.risk_v2_1 import VERSION as RISK_V2_1_VERSION
from research.structured_validation import SCREENING_PROMPT_VERSION
from scripts.finalize_monday_v2_shadow import (
    BASE_REPORT,
    BASE_RUN_ID,
    CHECKPOINT,
    DELIVERABLES,
    DISCLAIMER,
    FULL_UNIVERSE,
    OUTPUT_ROOT,
    TRADE_DATE,
    _canonical_hash,
    _flash_context,
    _pro_compact,
    audit_risk_lineage,
    build_theme_membership,
    stock_code,
)
from scripts.run_tushare_quant_v2_validation import read_json
from trader_demo.pro_single_v3 import SINGLE_CONTRACT_VERSION, SINGLE_PROMPT_VERSION


TARGET_TRADE_DATE = "2026-07-27"
FACTOR_VERSION = "TUSHARE_QUANT_V2_CORRECTED_SHADOW"
FLASH_CONTRACT_VERSION = "flash_component_wire_v4"
FREEZE_ROOT = OUTPUT_ROOT / "frozen_decisions"
FORWARD_ROOT = OUTPUT_ROOT / "forward_tracking"
RISK_SPEC = {
    "version": RISK_V2_1_VERSION,
    "base_version": "RISK_V2_FROZEN_20260724",
    "status": "SHADOW_DEFINITION_READY_NOT_APPLIED",
    "components_added": [
        "20日最大回撤",
        "下行波动",
        "筹码获利盘和成本位置",
        "解禁、减持、质押及重大财务事件",
    ],
    "missing_policy": "ZERO_CONTRIBUTION_NO_RENORMALIZATION",
    "pipeline_error_policy": "INVALID_NO_SCORE",
    "historical_2026_07_24_override": False,
    "production_enabled": False,
}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    ).encode("utf-8")


def _csv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    values = [dict(row) for row in rows]
    fields: list[str] = []
    for row in values:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["status"]
        values = [{"status": "EMPTY"}]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(values)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def _write_immutable(path: Path, content: bytes, *, readonly: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"IMMUTABLE_CONTENT_MISMATCH:{path}")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o444 if readonly else 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise
    if readonly:
        path.chmod(stat.S_IREAD)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_sources() -> dict[str, Any]:
    base = read_json(BASE_REPORT, {})
    final = read_json(DELIVERABLES["audit"], {})
    checkpoint = read_json(CHECKPOINT, {})
    if base.get("run_id") != BASE_RUN_ID or final.get("base_run_id") != BASE_RUN_ID:
        raise ValueError("SOURCE_RUN_ID_MISMATCH")
    if final.get("final_status") != "MONDAY_V2_SHADOW_FINALIZED":
        raise ValueError("FINALIZED_SOURCE_REQUIRED")
    if checkpoint.get("base_run_id") != BASE_RUN_ID:
        raise ValueError("CHECKPOINT_SOURCE_RUN_ID_MISMATCH")
    stage = base["v2_run"]["stages"][FACTOR_VERSION]
    return {
        "base": base,
        "final": final,
        "checkpoint": checkpoint,
        "top20": list(stage["top20"]),
        "top100": list(stage["top100"]),
    }


def audit_checkpoint(sources: Mapping[str, Any]) -> dict[str, Any]:
    base = sources["base"]
    final = sources["final"]
    checkpoint = sources["checkpoint"]
    top100 = [dict(row) for row in sources["top100"] if not row.get("hard_gate")]
    top100_by = {stock_code(row["stock_code"]): row for row in top100}
    _, _, risk_summary = audit_risk_lineage(base, sources["top20"])
    _, theme_by = build_theme_membership(top100)
    rows: list[dict[str, Any]] = []
    stale = 0
    missing_raw_binding = 0
    for item, saved in sorted(checkpoint["flash"].items()):
        source = top100_by.get(item)
        if source is None:
            expected_hash = None
            matched = False
        else:
            expected_hash = _canonical_hash(
                _flash_context(source, theme_by[item], risk_summary.get(item) or {})
            )
            matched = expected_hash == saved.get("context_hash")
        stale += int(not matched)
        missing_raw_binding += int(
            any(
                field not in saved
                for field in ("trade_date", "factor_version", "contract_version")
            )
        )
        rows.append(
            {
                "stage": "FLASH",
                "trade_date": TRADE_DATE,
                "stock_code": item,
                "factor_version": FACTOR_VERSION,
                "input_hash": saved.get("context_hash"),
                "recomputed_input_hash": expected_hash,
                "input_hash_match": matched,
                "prompt_version": saved.get("prompt_version"),
                "expected_prompt_version": SCREENING_PROMPT_VERSION,
                "contract_version": FLASH_CONTRACT_VERSION,
                "execution_status": saved.get("execution_status"),
                "raw_checkpoint_binding_complete": False,
                "audit_sidecar_binding_complete": True,
            }
        )
    flash_by = {
        stock_code(row["stock_code"]): row
        for row in final["flash"]["top20"]
    }
    for item, saved in sorted(checkpoint["pro"].items()):
        source = top100_by.get(item)
        flash = flash_by.get(item)
        if source is None or flash is None:
            expected_hash = None
            matched = False
        else:
            expected_hash = _canonical_hash(
                _pro_compact(source, flash, theme_by[item])
            )
            matched = expected_hash == saved.get("input_hash")
        stale += int(not matched)
        missing_raw_binding += int(
            any(
                field not in saved
                for field in ("trade_date", "factor_version")
            )
        )
        rows.append(
            {
                "stage": "PRO",
                "trade_date": TRADE_DATE,
                "stock_code": item,
                "factor_version": FACTOR_VERSION,
                "input_hash": saved.get("input_hash"),
                "recomputed_input_hash": expected_hash,
                "input_hash_match": matched,
                "prompt_version": saved.get("prompt_version"),
                "expected_prompt_version": SINGLE_PROMPT_VERSION,
                "contract_version": saved.get("contract_version"),
                "expected_contract_version": SINGLE_CONTRACT_VERSION,
                "execution_status": saved.get("execution_status"),
                "raw_checkpoint_binding_complete": False,
                "audit_sidecar_binding_complete": True,
            }
        )
    flash_network = int(final["flash"].get("api_calls") or 0)
    pro_network = int(final["pro"].get("api_calls") or 0)
    logical = len(checkpoint["flash"]) + len(checkpoint["pro"])
    derived_reuse = logical
    reported_reuse = int(final["flash"].get("reused_successful_checkpoint") or 0) + int(
        final["pro"].get("reused_successful_checkpoint") or 0
    )
    return {
        "status": "STALE_LLM_CHECKPOINT_FOUND" if stale else "PASS",
        "actual_network_calls": flash_network + pro_network,
        "logical_evaluations": logical,
        "new_business_calls_final_resume": int(
            final["flash"].get("new_business_calls") or 0
        )
        + int(final["pro"].get("new_business_calls") or 0),
        "reused_checkpoint_count": derived_reuse,
        "reported_reused_checkpoint_count": reported_reuse,
        "reported_reuse_counter_scope": (
            "Final local resume pass. Flash report recorded Top20=20 instead of "
            "the 86 reused Flash successes; sidecar derives 86+20 from checkpoint."
        ),
        "reused_input_hash_match": sum(bool(row["input_hash_match"]) for row in rows),
        "stale_checkpoint_count": stale,
        "raw_checkpoint_missing_binding_count": missing_raw_binding,
        "binding_sidecar_rows": rows,
        "llm_audit_quality": "QUESTIONABLE" if stale else "PASS",
        "no_llm_rerun": True,
    }


def _layer_rows(sources: Mapping[str, Any]) -> list[dict[str, Any]]:
    final = sources["final"]
    base = sources["base"]
    layers = [
        ("ACTIVE_SHADOW", final["active_shadow"]),
        ("WATCH_POOL", final["watch_pool"]),
        ("FLASH_TOP20", final["flash"]["top20"]),
        ("V2_TOP100", base["v2_run"]["stages"][FACTOR_VERSION]["top100"]),
    ]
    output: list[dict[str, Any]] = []
    for layer, rows in layers:
        for layer_rank, source in enumerate(rows, 1):
            output.append(
                {
                    "layer": layer,
                    "layer_rank": layer_rank,
                    "stock_code": stock_code(source.get("stock_code")),
                    "stock_name": source.get("stock_name"),
                    "quant_rank": source.get("v2_rank")
                    or source.get("quant_rank")
                    or source.get("rank"),
                    "quant_score": source.get("v2_score")
                    or source.get("quant_score")
                    or source.get("total_score"),
                    "hard_gate": source.get("hard_gate", False),
                    "decision_status": (
                        source.get("deployment_status")
                        or source.get("screening_decision")
                        or "RANKED"
                    ),
                    "trade_date": TRADE_DATE,
                    "target_trade_date": TARGET_TRADE_DATE,
                    "factor_version": FACTOR_VERSION,
                }
            )
    return output


def freeze_decision(
    sources: Mapping[str, Any],
    checkpoint_audit: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], Path]:
    final = sources["final"]
    base = sources["base"]
    active = [
        {
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "reference_price": row["recommended_price"],
            "industry": row["industry"],
            "binding_cluster": row["binding_cluster"],
            "concentration_reason": row["concentration_reason"],
        }
        for row in final["active_shadow"]
    ]
    expected = {"002414": "14.96", "000603": "18.33"}
    if {row["stock_code"]: str(row["reference_price"]) for row in active} != expected:
        raise ValueError("ACTIVE_CANDIDATE_FREEZE_MISMATCH")
    source_hashes = {
        "base_report": _file_hash(BASE_REPORT),
        "final_candidate_audit": _file_hash(DELIVERABLES["audit"]),
        "llm_checkpoint": _file_hash(CHECKPOINT),
        "full_universe": _file_hash(FULL_UNIVERSE),
        "watch_pool": _file_hash(DELIVERABLES["watch"]),
        "active_shadow": _file_hash(DELIVERABLES["active"]),
        "risk_lineage": _file_hash(DELIVERABLES["risk"]),
        "theme_concentration": _file_hash(DELIVERABLES["theme"]),
    }
    universe_hash = source_hashes["full_universe"]
    universe_snapshot_id = f"v2-universe-20260724-{universe_hash[:20]}"
    decision_input = {
        "source_run_id": BASE_RUN_ID,
        "trade_date": TRADE_DATE,
        "target_trade_date": TARGET_TRADE_DATE,
        "market_regime": base["v2_run"]["global_regime"],
        "factor_version": FACTOR_VERSION,
        "universe_snapshot_id": universe_snapshot_id,
        "source_hashes": source_hashes,
        "model_hashes": dict(base["hash_audit"]),
        "active_candidates": active,
        "watch_pool": final["watch_pool"],
        "flash_top20": final["flash"]["top20"],
        "v2_top100": base["v2_run"]["stages"][FACTOR_VERSION]["top100"],
        "order_plans": final["order_plans"],
        "theme_concentration": _read_csv(DELIVERABLES["theme"]),
        "checkpoint_audit_summary": {
            key: checkpoint_audit[key]
            for key in (
                "status",
                "actual_network_calls",
                "logical_evaluations",
                "reused_checkpoint_count",
                "reused_input_hash_match",
                "stale_checkpoint_count",
            )
        },
        "sample_layer_counts": {
            layer: sum(row["layer"] == layer for row in samples)
            for layer in ("ACTIVE_SHADOW", "WATCH_POOL", "FLASH_TOP20", "V2_TOP100")
        },
        "advisory_only": True,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
        "disclaimer": DISCLAIMER,
    }
    input_hash = _hash(decision_input)
    core = {
        **decision_input,
        "input_hash": input_hash,
        "llm_audit_status": checkpoint_audit["llm_audit_quality"],
        "frozen_at": datetime.fromtimestamp(
            DELIVERABLES["audit"].stat().st_mtime, tz=timezone.utc
        ).isoformat(),
        "immutability_policy": "CONTENT_ADDRESSED_O_EXCL_READ_ONLY_NO_OVERWRITE",
    }
    content_hash = _hash(core)
    decision_id = f"monday-v2-20260727-{content_hash[:20]}"
    snapshot = {
        **core,
        "decision_id": decision_id,
        "content_hash": content_hash,
    }
    folder = FREEZE_ROOT / decision_id
    _write_immutable(
        folder / "decision_snapshot.json", _json_bytes(snapshot), readonly=True
    )
    return snapshot, folder


def _cost_config() -> dict[str, float]:
    config = yaml.safe_load((ROOT / "config" / "paper_trading.yaml").read_text("utf-8"))
    values = config["paper_trading"]["cost"]
    return {
        "slippage_rate": float(values["slippage_rate"]),
        "commission_rate": float(values["commission_rate"]),
        "stamp_tax_rate": float(values["stamp_tax_rate"]),
    }


def _trade_dates(as_of: date) -> list[date]:
    root = ROOT / "data" / "cache" / "tushare" / "trade_date" / "daily"
    output = []
    for path in root.glob("*.json"):
        try:
            value = date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:8]))
        except (ValueError, IndexError):
            continue
        if date.fromisoformat(TARGET_TRADE_DATE) <= value <= as_of:
            output.append(value)
    return sorted(set(output))


def _daily(kind: str, day: date) -> dict[str, dict[str, Any]]:
    path = (
        ROOT
        / "data"
        / "cache"
        / "tushare"
        / "trade_date"
        / kind
        / f"{day:%Y%m%d}.json"
    )
    rows = read_json(path, [])
    return {stock_code(row.get("ts_code")): dict(row) for row in rows}


def evaluate_forward(
    snapshot: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    days = _trade_dates(as_of)
    costs = _cost_config()
    plans = {
        stock_code(row["stock_code"]): row for row in snapshot["order_plans"]
    }
    required_codes = {row["stock_code"] for row in samples}
    gate = {
        "target_trade_date": TARGET_TRADE_DATE,
        "as_of_date": as_of.isoformat(),
        "available_trade_dates": [item.isoformat() for item in days],
        "daily_ready": bool(days and days[0].isoformat() == TARGET_TRADE_DATE),
        "external_api_calls": 0,
        "llm_calls": 0,
        "quant_runs": 0,
    }
    results = []
    for sample in samples:
        item = sample["stock_code"]
        base = {
            **dict(sample),
            "decision_id": snapshot["decision_id"],
            "execution_policy": "LEGAL_PLAN_PRICE_ELSE_T1_OPEN_PLUS_CONFIG_SLIPPAGE",
            "slippage_rate": costs["slippage_rate"],
            "entry_price": "",
            "entry_price_source": "",
            "tradability": "DATA_NOT_READY",
            "d1_net_return": "",
            "d1_mfe": "",
            "d1_mae": "",
            "reached_5pct": "",
            "stop_loss_triggered": "",
            "giveback": "",
            "current_status": "PENDING_D1",
            "d1_status": "PENDING",
            "d3_status": "PENDING",
            "d5_status": "PENDING",
        }
        if not gate["daily_ready"]:
            results.append(base)
            continue
        entry_bar = _daily("daily", days[0]).get(item)
        limit = _daily("stk_limit", days[0]).get(item, {})
        if not entry_bar:
            base.update(tradability="NOT_TRADABLE_OR_DATA_MISSING", current_status="DATA_MISSING")
            results.append(base)
            continue
        if float(entry_bar.get("vol") or 0) <= 0:
            base.update(tradability="NOT_TRADABLE_SUSPENDED", current_status="NOT_TRADABLE")
            results.append(base)
            continue
        if (
            limit.get("up_limit") is not None
            and float(entry_bar["open"]) >= float(limit["up_limit"]) - 1e-8
            and float(entry_bar.get("low") or entry_bar["open"])
            >= float(limit["up_limit"]) - 1e-8
        ):
            base.update(tradability="NOT_TRADABLE_LOCKED_LIMIT_UP", current_status="NOT_TRADABLE")
            results.append(base)
            continue
        plan = plans.get(item)
        if plan:
            reference = float(plan["recommended_price"])
            if float(entry_bar["open"]) <= reference:
                entry = min(
                    float(entry_bar["open"]) * (1 + costs["slippage_rate"]),
                    reference,
                )
                source = "LEGAL_PLAN_FILL_AT_OPEN"
            elif float(entry_bar["low"]) <= reference:
                entry = reference
                source = "LEGAL_PLAN_LIMIT_TOUCHED"
            else:
                entry = float(entry_bar["open"]) * (1 + costs["slippage_rate"])
                source = "T1_OPEN_PLUS_SLIPPAGE_PLAN_FILL_UNCONFIRMED"
        else:
            entry = float(entry_bar["open"]) * (1 + costs["slippage_rate"])
            source = "T1_OPEN_PLUS_CONFIG_SLIPPAGE"
        base.update(
            entry_price=round(entry, 6),
            entry_price_source=source,
            tradability="TRADABLE",
            current_status="OPEN",
        )
        stop = float(plan["stop_loss_price"]) if plan and plan.get("stop_loss_price") else None
        for horizon in (1, 3, 5):
            status_key = f"d{horizon}_status"
            if len(days) < horizon:
                base[status_key] = "PENDING"
                continue
            bars = [_daily("daily", day).get(item) for day in days[:horizon]]
            if any(bar is None for bar in bars):
                base[status_key] = "DATA_MISSING"
                continue
            exit_bar = bars[-1]
            assert exit_bar is not None
            gross = float(exit_bar["close"]) / entry - 1
            net = gross - costs["commission_rate"] * 2 - costs["stamp_tax_rate"]
            mfe = max(float(bar["high"]) / entry - 1 for bar in bars if bar)
            mae = min(float(bar["low"]) / entry - 1 for bar in bars if bar)
            base[status_key] = "MATURED"
            base[f"d{horizon}_net_return"] = round(net, 8)
            base[f"d{horizon}_mfe"] = round(mfe, 8)
            base[f"d{horizon}_mae"] = round(mae, 8)
            base[f"d{horizon}_reached_5pct"] = mfe >= 0.05
            base[f"d{horizon}_stop_loss_triggered"] = bool(
                stop is not None and any(float(bar["low"]) <= stop for bar in bars if bar)
            )
            base[f"d{horizon}_giveback"] = round(mfe - net, 8)
            if horizon == 1:
                base.update(
                    d1_net_return=base["d1_net_return"],
                    d1_mfe=base["d1_mfe"],
                    d1_mae=base["d1_mae"],
                    reached_5pct=base["d1_reached_5pct"],
                    stop_loss_triggered=base["d1_stop_loss_triggered"],
                    giveback=base["d1_giveback"],
                    current_status="D1_MATURED",
                )
        results.append(base)
    summary: dict[str, Any] = {
        "gate": gate,
        "layer_counts": {
            layer: sum(row["layer"] == layer for row in results)
            for layer in ("ACTIVE_SHADOW", "WATCH_POOL", "FLASH_TOP20", "V2_TOP100")
        },
        "d1_matured": sum(row["d1_status"] == "MATURED" for row in results),
        "d3_matured": sum(row["d3_status"] == "MATURED" for row in results),
        "d5_matured": sum(row["d5_status"] == "MATURED" for row in results),
    }
    for layer in ("ACTIVE_SHADOW", "WATCH_POOL", "FLASH_TOP20", "V2_TOP100"):
        values = [
            float(row["d1_net_return"])
            for row in results
            if row["layer"] == layer and row["d1_status"] == "MATURED"
        ]
        summary[f"{layer.lower()}_average_return"] = (
            round(sum(values) / len(values), 8) if values else None
        )
    summary["required_code_count"] = len(required_codes)
    return results, summary


def _report(
    snapshot: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    outcome: Mapping[str, Any],
    *,
    final_status: str,
) -> dict[str, Any]:
    return {
        "Decision frozen": True,
        "Decision id": snapshot["decision_id"],
        "Decision hash": snapshot["content_hash"],
        "Checkpoint audit": checkpoint["status"],
        "Actual network calls": checkpoint["actual_network_calls"],
        "Reused checkpoints": checkpoint["reused_checkpoint_count"],
        "Stale checkpoints": checkpoint["stale_checkpoint_count"],
        "Active candidates": [
            f"{row['stock_name']} {row['stock_code']} @ {row['reference_price']}"
            for row in snapshot["active_candidates"]
        ],
        "Monday tradability": (
            "READY" if outcome["gate"]["daily_ready"] else "DATA_NOT_READY"
        ),
        "D1 matured": outcome["d1_matured"],
        "D3 matured": outcome["d3_matured"],
        "D5 matured": outcome["d5_matured"],
        "Active average return": outcome["active_shadow_average_return"],
        "Watch pool average return": outcome["watch_pool_average_return"],
        "Flash Top20 average return": outcome["flash_top20_average_return"],
        "V2 Top100 average return": outcome["v2_top100_average_return"],
        "Risk V2.1 status": RISK_SPEC["status"],
        "Production changes": "NONE",
        "Orders": "real=0; virtual=0",
        "Scheduler": "OFF",
        "Final status": final_status,
    }


def run(as_of: date) -> dict[str, Any]:
    sources = _load_sources()
    checkpoint = audit_checkpoint(sources)
    samples = _layer_rows(sources)
    snapshot, folder = freeze_decision(sources, checkpoint, samples)
    checkpoint_path = folder / "llm_checkpoint_binding_audit.json"
    sample_path = folder / "forward_sample_layers.csv"
    risk_path = folder / "risk_v2_1_shadow_spec.json"
    _write_immutable(checkpoint_path, _json_bytes(checkpoint), readonly=True)
    _write_immutable(sample_path, _csv_bytes(samples), readonly=True)
    _write_immutable(risk_path, _json_bytes(RISK_SPEC), readonly=True)

    outcomes, outcome_summary = evaluate_forward(snapshot, samples, as_of=as_of)
    tracking = FORWARD_ROOT / snapshot["decision_id"]
    outcome_content_hash = _hash(
        {"as_of_date": as_of, "outcomes": outcomes, "summary": outcome_summary}
    )
    outcome_suffix = f"{as_of:%Y%m%d}_{outcome_content_hash[:12]}"
    outcome_path = tracking / f"forward_outcomes_{outcome_suffix}.csv"
    outcome_summary_path = tracking / f"forward_summary_{outcome_suffix}.json"
    outcome_summary = {
        **outcome_summary,
        "outcome_content_hash": outcome_content_hash,
    }
    _write_immutable(outcome_path, _csv_bytes(outcomes))
    _write_immutable(outcome_summary_path, _json_bytes(outcome_summary))

    if checkpoint["stale_checkpoint_count"]:
        final_status = "STALE_LLM_CHECKPOINT_FOUND"
    elif outcome_summary["d1_matured"]:
        final_status = "FORWARD_D1_RECORDED"
    else:
        final_status = "MONDAY_DECISION_FROZEN"
    report = _report(snapshot, checkpoint, outcome_summary, final_status=final_status)
    report.update(
        {
            "snapshot_path": str(folder / "decision_snapshot.json"),
            "checkpoint_audit_path": str(checkpoint_path),
            "sample_layers_path": str(sample_path),
            "outcome_path": str(outcome_path),
            "outcome_summary_path": str(outcome_summary_path),
            "risk_v2_1_spec_path": str(risk_path),
            "runtime_audit": {
                "quant_runs": 0,
                "flash_calls": 0,
                "pro_calls": 0,
                "external_api_calls": 0,
                "orders": 0,
                "scheduler": False,
            },
        }
    )
    report_path = tracking / f"freeze_forward_report_{outcome_suffix}.json"
    markdown_path = tracking / f"freeze_forward_report_{outcome_suffix}.md"
    _write_immutable(report_path, _json_bytes(report))
    markdown = "\n".join(
        ["# Freeze Monday V2 Decision", ""]
        + [f"- {key}: {value}" for key, value in report.items() if not key.endswith("_path")]
        + ["", DISCLAIMER, ""]
    )
    _write_immutable(markdown_path, markdown.encode("utf-8"))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    try:
        result = run(args.as_of_date)
    except FileExistsError as exc:
        print(json.dumps({"Final status": "DECISION_SNAPSHOT_FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(json.dumps({"Final status": "BLOCKED", "error": f"{type(exc).__name__}:{exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
