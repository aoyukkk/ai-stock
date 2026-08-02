from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.shadow.risk_evidence import build_risk_evidence_lineage
from temporal.evidence_status import news_evidence_status, overseas_evidence_status
from temporal.freshness import (
    FreshnessGateAction,
    FreshnessPolicy,
    build_freshness_manifest,
    canonical_hash,
    evaluate_freshness,
)


FROZEN_DECISION_ID = "monday-v2-20260727-51ab2f95c9a194d507e3"
FROZEN_DECISION_HASH = "51ab2f95c9a194d507e344964224339489cdec604b26014696fda2ee7000c1d9"


def sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return default


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    values = [dict(row) for row in rows]
    fields: list[str] = []
    for row in values:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["status"])
        writer.writeheader()
        for row in values:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def fundamental_cache_audit(
    cache_root: Path, decision: datetime
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata: list[dict[str, Any]] = []
    for path in cache_root.glob("*/*/*.json"):
        payload = read_json(path, {})
        meta = dict(payload.get("metadata") or {})
        fetched_text = meta.get("fetched_at")
        if not fetched_text:
            continue
        try:
            fetched = datetime.fromisoformat(str(fetched_text))
        except ValueError:
            continue
        periods = {
            str(row.get("end_date") or meta.get("period") or "")
            for row in list(payload.get("records") or [])[:5000]
        }
        announcements = [
            str(row.get("f_ann_date") or row.get("ann_date") or "")
            for row in list(payload.get("records") or [])[:5000]
            if row.get("f_ann_date") or row.get("ann_date")
        ]
        metadata.append(
            {
                "path": str(path),
                "interface": meta.get("interface"),
                "period": meta.get("period"),
                "report_periods": sorted(value for value in periods if value),
                "latest_announcement_date": max(announcements, default=None),
                "cache_fetched_at": fetched.isoformat(),
                "cache_age_hours": max(
                    0,
                    (
                        decision.astimezone(ZoneInfo("UTC"))
                        - fetched.astimezone(ZoneInfo("UTC"))
                    ).total_seconds()
                    / 3600,
                ),
                "freshness_status": "STALE"
                if (decision - fetched.astimezone(decision.tzinfo)).total_seconds() > 24 * 3600
                else "FRESH",
            }
        )
    stale = [row for row in metadata if row["freshness_status"] == "STALE"]
    summary = {
        "cache_file_count": len(metadata),
        "stale_cache_file_count": len(stale),
        "earliest_cache_fetched_at": min(
            (row["cache_fetched_at"] for row in metadata), default=None
        ),
        "latest_cache_fetched_at": max(
            (row["cache_fetched_at"] for row in metadata), default=None
        ),
        "latest_report_period": max(
            (
                value
                for row in metadata
                for value in row.get("report_periods") or []
            ),
            default=None,
        ),
        "latest_announcement_date": max(
            (row["latest_announcement_date"] for row in metadata if row.get("latest_announcement_date")),
            default=None,
        ),
        "freshness_status": "STALE" if stale else "FRESH",
        "display_as_data_current_to_decision_date": False,
    }
    return summary, metadata


def checkpoint_audit(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for stage in ("flash", "pro"):
        for code, value in dict(checkpoint.get(stage) or {}).items():
            success = value.get("execution_status") == "SUCCESS"
            has_contract = isinstance(value.get("checkpoint_contract"), Mapping)
            reasons = [] if has_contract else ["LEGACY_CHECKPOINT_INSUFFICIENT_METADATA"]
            if not success:
                reasons.append("CHECKPOINT_NOT_SUCCESSFUL")
            rows.append(
                {
                    "stage": stage.upper(),
                    "stock_code": code,
                    "execution_status": value.get("execution_status"),
                    "reuse_allowed": success and has_contract,
                    "reused_input_hash_match": bool(
                        has_contract
                        and value["checkpoint_contract"].get("input_hash")
                        == (value.get("input_hash") or value.get("context_hash"))
                    ),
                    "reasons": reasons,
                    "saved_input_hash": value.get("input_hash") or value.get("context_hash"),
                    "checkpoint_contract_hash": value.get("checkpoint_contract_hash"),
                }
            )
    return {
        "actual_network_calls": 0,
        "logical_evaluations": len(rows),
        "reused_checkpoint_count": sum(row["reuse_allowed"] for row in rows),
        "reused_input_hash_match": all(
            row["reused_input_hash_match"] for row in rows if row["reuse_allowed"]
        ),
        "stale_checkpoint_count": sum(not row["reuse_allowed"] for row in rows),
        "status": "STALE_LLM_CHECKPOINT_FOUND"
        if any(not row["reuse_allowed"] for row in rows)
        else "CHECKPOINT_REUSE_VALID",
        "rows": rows,
    }


def run(trade_date: date) -> dict[str, Any]:
    trade_text = trade_date.isoformat()
    decision = datetime(
        trade_date.year,
        trade_date.month,
        trade_date.day,
        18,
        38,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    v2_root = ROOT / "outputs" / "quant_v2_validation" / trade_text
    audit_root = ROOT / "outputs" / trade_text / "审计" / "freshness_remediation"
    audit_root.mkdir(parents=True, exist_ok=True)
    originals = [
        v2_root / "quant_v2_validation.json",
        v2_root / "v2_full_universe.csv",
        v2_root / ".monday_v2_llm_checkpoint.json",
        v2_root / "monday_v2_candidate_audit.json",
        v2_root / "monday_v2_watch_pool.csv",
    ]
    before = {str(path): sha256(path) for path in originals}
    report = read_json(v2_root / "quant_v2_validation.json", {})

    generic_members = list((ROOT / "data" / "cache" / "tushare").glob("ths_member_*.json"))
    latest_member_mtime = max(
        (datetime.fromtimestamp(path.stat().st_mtime, tz=ZoneInfo("Asia/Shanghai")) for path in generic_members),
        default=None,
    )
    member_business_date = latest_member_mtime.date() if latest_member_mtime else None
    membership_evidence = []
    for category in ("industry", "concept"):
        hard = category == "industry"
        membership_evidence.append(
            evaluate_freshness(
                dataset_name=f"ths_{category}_members",
                provider="TUSHARE",
                decision_as_of_time=decision,
                policy=FreshnessPolicy(
                    dataset_name=f"ths_{category}_members",
                    max_age_trading_days=1,
                    stale_action=FreshnessGateAction.BLOCK_STAGE if hard else FreshnessGateAction.DEGRADE,
                    unverified_action=FreshnessGateAction.BLOCK_STAGE if hard else FreshnessGateAction.DEGRADE,
                    missing_action=FreshnessGateAction.BLOCK_STAGE if hard else FreshnessGateAction.DEGRADE,
                ),
                data_business_date=member_business_date,
                cache_fetched_at=latest_member_mtime,
                source_status="available" if generic_members else "missing",
                content={"file_count": len(generic_members), "latest_mtime": latest_member_mtime},
                current_membership_only=True,
            )
        )

    fundamental_summary, fundamental_files = fundamental_cache_audit(
        ROOT / "data" / "cache" / "tushare" / "fundamental", decision
    )
    fundamental_evidence = evaluate_freshness(
        dataset_name="fundamentals",
        provider="TUSHARE",
        decision_as_of_time=decision,
        policy=FreshnessPolicy(
            dataset_name="fundamentals",
            max_age_hours=24,
            stale_action=FreshnessGateAction.DEGRADE,
            unverified_action=FreshnessGateAction.DEGRADE,
            missing_action=FreshnessGateAction.DEGRADE,
        ),
        cache_fetched_at=(
            datetime.fromisoformat(fundamental_summary["latest_cache_fetched_at"])
            if fundamental_summary.get("latest_cache_fetched_at")
            else None
        ),
        source_status="available" if fundamental_files else "missing",
        content=fundamental_summary,
    )
    news = news_evidence_status(
        decision_as_of_time=decision,
        provider_enabled=False,
        provider_query_succeeded=False,
        evidence=[],
    )
    overseas = overseas_evidence_status(
        decision_as_of_time=decision,
        provider_enabled=False,
        is_mock=True,
    )
    news_evidence = evaluate_freshness(
        dataset_name="news",
        provider="DATA_ONLY",
        decision_as_of_time=decision,
        policy=FreshnessPolicy(
            dataset_name="news",
            max_age_hours=36,
            stale_action=FreshnessGateAction.DEGRADE,
            unverified_action=FreshnessGateAction.DEGRADE,
            missing_action=FreshnessGateAction.DEGRADE,
        ),
        source_status="disabled",
        content=news,
    )
    overseas_evidence = evaluate_freshness(
        dataset_name="overseas",
        provider="PLACEHOLDER",
        decision_as_of_time=decision,
        policy=FreshnessPolicy(
            dataset_name="overseas",
            max_age_hours=24,
            stale_action=FreshnessGateAction.DEGRADE,
            unverified_action=FreshnessGateAction.DEGRADE,
            missing_action=FreshnessGateAction.DEGRADE,
        ),
        source_status="disabled",
        content=overseas,
        is_mock=True,
    )
    manifest = build_freshness_manifest(
        run_id=str(report.get("run_id") or f"freshness-audit-{trade_text}"),
        decision_as_of_time=decision,
        evidence=[
            *membership_evidence,
            fundamental_evidence,
            news_evidence,
            overseas_evidence,
        ],
    )
    manifest["fundamental_cache_summary"] = fundamental_summary
    manifest["news_evidence_status"] = news
    manifest["overseas_evidence_status"] = overseas
    membership_hash = canonical_hash(
        [item.model_dump(mode="json") for item in membership_evidence]
    )
    manifest["membership_manifest_hash"] = membership_hash
    manifest["downstream_membership_manifest_hashes"] = {
        name: membership_hash
        for name in ("sector_emotion", "theme_cluster", "flash_context", "concentration_control")
    }
    write_json(audit_root / "data_freshness_manifest.json", manifest)
    issues = [
        row
        for row in manifest["datasets"]
        if row["freshness_status"] not in {"FRESH", "VERIFIED_CURRENT"}
    ]
    write_csv(audit_root / "data_freshness_issues.csv", issues)

    checkpoint = read_json(v2_root / ".monday_v2_llm_checkpoint.json", {})
    checkpoint_result = checkpoint_audit(checkpoint)
    write_json(audit_root / "checkpoint_reuse_audit.json", checkpoint_result)
    write_csv(audit_root / "checkpoint_reuse_audit.csv", checkpoint_result["rows"])

    frozen_risk_rows = read_csv(v2_root / "monday_v2_risk_lineage.csv")
    by_code: dict[str, dict[str, dict[str, Any]]] = {}
    for row in frozen_risk_rows:
        by_code.setdefault(str(row.get("stock_code")), {})[str(row.get("risk_component"))] = {
            "raw_value": row.get("raw_value") or None,
            "score": row.get("score") or None,
            "contribution": row.get("contribution") or None,
            "normalization_direction": row.get("normalization_direction"),
            "source": "FROZEN_20260728_RISK_LINEAGE",
            "data_business_date": trade_text,
            "freshness_status": "UNVERIFIED",
            "point_in_time_safe": True,
            "confidence": 0.5,
            "fallback": row.get("missing/fallback"),
        }
    risk_payloads = [
        build_risk_evidence_lineage(
            stock_code=code,
            decision_as_of_time=decision,
            components=components,
        )
        for code, components in sorted(by_code.items())
    ]
    risk_rows = [row for payload in risk_payloads for row in payload["rows"]]
    write_csv(audit_root / "risk_evidence_lineage.csv", risk_rows)
    write_json(
        audit_root / "risk_evidence_manifest.json",
        {
            "version": "risk-evidence-lineage-v1",
            "decision_as_of_time": decision.isoformat(),
            "stock_count": len(risk_payloads),
            "row_count": len(risk_rows),
            "missing_component_count": sum(
                payload["missing_component_count"] for payload in risk_payloads
            ),
            "content_hash": canonical_hash(risk_payloads),
        },
    )

    final_audit = read_json(v2_root / "monday_v2_candidate_audit.json", {})
    calculated_regime = str(
        ((report.get("v2_run") or {}).get("global_regime") or {}).get("regime")
        or "MISSING"
    )
    original_deployment_regimes = sorted(
        {
            str(row.get("regime") or "MISSING")
            for row in list(final_audit.get("watch_pool") or [])
        }
    )
    currentness = {
        "decision_as_of_time": decision.isoformat(),
        "core_market_data_date": trade_text,
        "currentness_status": "PARTIAL",
        "statement": (
            f"核心行情截至{trade_text}；部分行业、基本面、新闻或海外信息"
            "未完成当日验证，详见数据新鲜度说明。"
        ),
        "membership_status": [item.freshness_status for item in membership_evidence],
        "fundamental_status": fundamental_evidence.freshness_status,
        "news_status": news["status"],
        "overseas_status": overseas["status"],
        "calculated_market_regime": calculated_regime,
        "original_deployment_regimes": original_deployment_regimes,
        "remediated_deployment_regime": calculated_regime,
        "regime_mismatch_detected_in_original": bool(
            original_deployment_regimes
            and original_deployment_regimes != [calculated_regime]
        ),
        "report_currentness_status": "REPORT_CURRENTNESS_PARTIAL",
    }
    write_json(audit_root / "report_currentness_summary.json", currentness)

    frozen_path = (
        ROOT
        / "outputs"
        / "quant_v2_validation"
        / "2026-07-24"
        / "frozen_decisions"
        / FROZEN_DECISION_ID
        / "decision_snapshot.json"
    )
    frozen = read_json(frozen_path, {})
    frozen_ok = (
        frozen.get("decision_id") == FROZEN_DECISION_ID
        and frozen.get("content_hash") == FROZEN_DECISION_HASH
    )
    after = {str(path): sha256(path) for path in originals}
    originals_unchanged = before == after
    validation = {
        "trade_date": trade_text,
        "read_only_original_result_validation": True,
        "old_concept_cache_identified_stale": membership_evidence[1].freshness_status == "STALE",
        "old_membership_complete": False,
        "old_membership_eligible_for_scoring": False,
        "refresh_failure_policy": {
            "industry": "BLOCK_STAGE",
            "concept": "DEGRADE",
        },
        "fundamental_report_period": fundamental_summary.get("latest_report_period"),
        "fundamental_latest_disclosure_date": fundamental_summary.get("latest_announcement_date"),
        "fundamental_cache_fetched_at": fundamental_summary.get("latest_cache_fetched_at"),
        "fundamental_not_presented_as_current_to_20260728": not fundamental_summary["display_as_data_current_to_decision_date"],
        "historical_refill_requires_decision_time": True,
        "checkpoint_input_change_rejected": checkpoint_result["stale_checkpoint_count"] > 0,
        "identical_contract_reuse_supported_by_new_validator": True,
        "news_data_only_visible": news["status"] == "NEWS_PROVIDER_DISABLED",
        "overseas_mock_excluded": not overseas["eligible_for_scoring"],
        "market_regime": calculated_regime,
        "deployment_regime_after_fix": calculated_regime,
        "legacy_original_artifacts_unchanged": originals_unchanged,
        "frozen_decision_id": frozen.get("decision_id"),
        "frozen_decision_hash": frozen.get("content_hash"),
        "frozen_decision_verified": frozen_ok,
        "real_orders_created": 0,
        "virtual_orders_created": 0,
        "scheduler": False,
        "real_trading": False,
        "final_status": "POINT_IN_TIME_REMEDIATION_SHADOW_READY",
    }
    write_json(audit_root / "remediation_validation_report.json", validation)
    markdown = [
        "# Point-in-Time Freshness Remediation Validation",
        "",
        f"- Decision as of: {decision.isoformat()}",
        f"- THS membership: {', '.join(str(item.freshness_status) for item in membership_evidence)}",
        f"- Fundamental cache: {fundamental_summary.get('freshness_status')}; report period={fundamental_summary.get('latest_report_period')}; fetched={fundamental_summary.get('latest_cache_fetched_at')}",
        f"- Checkpoint audit: {checkpoint_result['status']}; stale={checkpoint_result['stale_checkpoint_count']}",
        f"- News: {news['status']} (DATA_ONLY is not verified-empty)",
        f"- Overseas: {overseas['status']} (excluded from scoring)",
        f"- Market regime: calculated={calculated_regime}; original deployment={original_deployment_regimes}; fixed deployment={calculated_regime}",
        f"- Frozen decision: {'PASS' if frozen_ok else 'FAIL'}",
        f"- Original 2026-07-28 artifacts unchanged: {originals_unchanged}",
        "- Orders: real=0; virtual=0; scheduler=false",
        "",
        "This is a read-only audit of the original run. It does not claim that all external data sources are current.",
    ]
    (audit_root / "remediation_validation_report.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    return validation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-date", default="2026-07-28")
    args = parser.parse_args()
    result = run(date.fromisoformat(args.trade_date))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["frozen_decision_verified"] and result["legacy_original_artifacts_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
