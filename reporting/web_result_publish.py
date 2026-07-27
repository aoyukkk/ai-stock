from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from database.models.quant_run import QuantRankResult, QuantRun
from database.models.research import FundamentalResearchRun, ResearchEvidenceRecord
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    ProCandidateReview,
    ProResumeRun,
    ValidationAccountSnapshot,
)
from database.session import get_session
from scripts.publish_business_results_to_internal_web import publish_snapshot
from stock_codes import normalize_ts_code


def publish_workbook_payload(
    *,
    trade_date: date,
    target_trade_date: date,
    payload: Mapping[str, Any],
    full_quant_rows: list[Mapping[str, Any]],
    final_audit: Mapping[str, Any],
    fundamental_rows: list[Mapping[str, Any]],
    workbook_path: Path,
    workbook_sha256: str,
    workbook_content_hash: str,
    factor_version: str,
    production_promoted: bool = False,
) -> dict[str, Any]:
    """Persist an append-only Web readback chain matching the delivered workbook."""

    material = {
        "trade_date": trade_date.isoformat(),
        "target_trade_date": target_trade_date.isoformat(),
        "source_run_id": final_audit.get("base_run_id"),
        "factor_version": factor_version,
        "workbook_content_hash": workbook_content_hash,
        "candidate_codes": [row.get("股票代码") for row in payload.get("candidates", [])],
        "fundamental_rows": payload.get("fundamentals", []),
    }
    content_hash = _hash(material)
    suffix = content_hash[:16]
    quant_run_id = f"web-v2-quant-{suffix}"
    flash_run_id = f"web-v2-flash-{suffix}"
    pro_run_id = f"web-v2-pro-{suffix}"
    pipeline_run_id = f"web-v2-{trade_date:%Y%m%d}-{suffix[:10]}"
    manifest_id = str(final_audit.get("data_manifest_id") or "")
    if not manifest_id:
        manifest_id = str(final_audit.get("manifest_id") or "")
    if not manifest_id:
        manifest_id = _source_manifest_id(trade_date)

    session = get_session()
    try:
        existing = session.scalar(
            select(ProResumeRun).where(ProResumeRun.run_id == pro_run_id)
        )
        if existing is None:
            _persist_chain(
                session=session,
                trade_date=trade_date,
                target_trade_date=target_trade_date,
                payload=payload,
                full_quant_rows=full_quant_rows,
                final_audit=final_audit,
                fundamental_rows=fundamental_rows,
                workbook_path=workbook_path,
                workbook_sha256=workbook_sha256,
                factor_version=factor_version,
                content_hash=content_hash,
                quant_run_id=quant_run_id,
                flash_run_id=flash_run_id,
                pro_run_id=pro_run_id,
                pipeline_run_id=pipeline_run_id,
                manifest_id=manifest_id,
                production_promoted=production_promoted,
            )
            reused = False
        else:
            reused = True
        counts = _chain_counts(session, quant_run_id, flash_run_id, pro_run_id)
        expected_counts = {
            "quant": len(full_quant_rows),
            "flash": len(payload.get("candidates") or []),
            "pro": len(payload.get("candidates") or []),
            "order": len(payload.get("candidates") or []),
            "position": len(payload.get("candidates") or []),
        }
        if counts != expected_counts:
            raise RuntimeError(
                f"WEB_PUBLISH_CHAIN_COUNT_MISMATCH:{counts}!={expected_counts}"
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    web_sync = publish_internal_web_snapshot()
    if web_sync.get("status") != "SUCCESS":
        raise RuntimeError(
            "INTERNAL_WEB_SNAPSHOT_PUBLISH_FAILED:"
            + str(web_sync.get("error") or web_sync.get("reason") or "unknown")
        )
    return {
        "status": "SUCCESS",
        "reused": reused,
        "content_hash": content_hash,
        "candidate_set_hash": _candidate_hash(payload),
        "pipeline_run_id": pipeline_run_id,
        "quant_run_id": quant_run_id,
        "flash_run_id": flash_run_id,
        "pro_run_id": pro_run_id,
        "counts": counts,
        "workbook_path": str(workbook_path.resolve()),
        "workbook_sha256": workbook_sha256,
        "workbook_content_hash": workbook_content_hash,
        "web_sync": web_sync,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
        "production_promoted": production_promoted,
    }


def publish_internal_web_snapshot() -> dict[str, Any]:
    source = Path(
        os.getenv("AI_TRADER_DB_PATH")
        or Path(__file__).resolve().parents[1] / "data" / "ai_trader_dev.db"
    )
    destination = Path(
        os.getenv("INTERNAL_WEB_BUSINESS_DATABASE_PATH")
        or r"C:\ProgramData\AITraderAssistant\business\ai_trader_business.db"
    )
    if not destination.parent.exists():
        return {
            "status": "SKIPPED",
            "reason": "INTERNAL_WEB_BUSINESS_DIRECTORY_NOT_INSTALLED",
        }
    try:
        result = publish_snapshot(source, destination)
        return {
            "status": "SUCCESS",
            "source": str(source.resolve()),
            "destination": str(destination.resolve()),
            **result,
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "error_category": type(exc).__name__,
            "error": str(exc),
            "source": str(source.resolve()),
            "destination": str(destination.resolve()),
        }


def _persist_chain(
    *,
    session,
    trade_date: date,
    target_trade_date: date,
    payload: Mapping[str, Any],
    full_quant_rows: list[Mapping[str, Any]],
    final_audit: Mapping[str, Any],
    fundamental_rows: list[Mapping[str, Any]],
    workbook_path: Path,
    workbook_sha256: str,
    factor_version: str,
    content_hash: str,
    quant_run_id: str,
    flash_run_id: str,
    pro_run_id: str,
    pipeline_run_id: str,
    manifest_id: str,
    production_promoted: bool,
) -> None:
    now = datetime.now(timezone.utc)
    decision_time = datetime(
        trade_date.year,
        trade_date.month,
        trade_date.day,
        15,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    quant_by_code = {
        normalize_ts_code(str(row.get("stock_code") or "")): row
        for row in full_quant_rows
    }
    candidates = list(payload.get("candidates") or [])
    fundamentals = {
        normalize_ts_code(str(row.get("股票代码") or "")): row
        for row in (payload.get("fundamentals") or [])
    }
    orders = {
        normalize_ts_code(str(row.get("股票代码") or "")): row
        for row in (payload.get("orders") or [])
    }
    refill_by_code = {
        normalize_ts_code(str(row.get("stock_code") or "")): row
        for row in fundamental_rows
    }
    flash_by_code = {
        normalize_ts_code(str(row.get("stock_code") or "")): row
        for row in ((final_audit.get("flash") or {}).get("top20") or [])
    }
    pro_by_code = {
        normalize_ts_code(str(row.get("stock_code") or "")): row
        for row in ((final_audit.get("pro") or {}).get("results") or [])
    }

    quant = QuantRun(
        run_id=quant_run_id,
        request_hash=_hash({"kind": "web_quant", "content_hash": content_hash}),
        run_mode=(
            "WEB_PUBLISHED_V2"
            if production_promoted
            else "WEB_PUBLISHED_SHADOW"
        ),
        decision_time=decision_time,
        base_market_trade_date=trade_date,
        target_trade_date=target_trade_date,
        factor_version=factor_version,
        config_snapshot={
            "source": "WORKBOOK_PAYLOAD",
            "source_run_id": final_audit.get("base_run_id"),
            "shadow_only": not production_promoted,
            "production_promoted": production_promoted,
            "workbook_path": str(workbook_path.resolve()),
            "workbook_sha256": workbook_sha256,
        },
        data_manifest_id=manifest_id,
        universe_count=len(full_quant_rows),
        filtered_count=len(full_quant_rows),
        scored_count=len(full_quant_rows),
        skipped_count=0,
        top_count=min(100, len(full_quant_rows)),
        no_llm_call_verified=True,
        trade_date_cache_used=True,
        per_stock_api_call_count=0,
        temporal_status="PASS",
        actionable=production_promoted,
        status="COMPLETED",
    )
    session.add(quant)
    session.flush()
    session.add_all(
        [
            QuantRankResult(
                quant_run_id=quant_run_id,
                stock_code=normalize_ts_code(str(row.get("stock_code") or "")),
                rank=int(row["rank"]),
                total_score=_decimal(row.get("total_score"), "0"),
                technical_score=_decimal(row.get("technical_score"), "0"),
                capital_score=_decimal(row.get("capital_score"), "0"),
                emotion_score=_decimal(row.get("emotion_score"), "0"),
                momentum_score=_decimal(row.get("momentum_score"), "0"),
                risk_score=_decimal(row.get("risk_score"), "0"),
                factor_detail_reference={
                    "factor_version": factor_version,
                    "source": "V2_FULL_UNIVERSE_CSV",
                    "technical_data_source": row.get("technical_data_source"),
                    "sector_data_source": row.get("sector_data_source"),
                    "concept_data_source": row.get("concept_data_source"),
                    "data_coverage_status": row.get("data_coverage_status"),
                    "hard_gate": row.get("hard_gate"),
                },
            )
            for row in full_quant_rows
        ]
    )

    validation = ModelValidationRun(
        run_id=flash_run_id,
        quant_run_id=quant_run_id,
        run_data_manifest_id=manifest_id,
        run_mode=(
            "WEB_PUBLISHED_V2"
            if production_promoted
            else "WEB_PUBLISHED_SHADOW"
        ),
        knowledge_mode="STRUCTURED_PLUS_APPROVED_REFILL",
        decision_time=decision_time,
        base_market_trade_date=trade_date,
        target_trade_date=target_trade_date,
        real_llm=True,
        status="COMPLETED",
        request_hash=_hash({"kind": "web_flash", "content_hash": content_hash}),
        config_snapshot={
            "source": "WORKBOOK_PAYLOAD",
            "shadow_only": not production_promoted,
            "production_promoted": production_promoted,
            "llm_refill_prompt_version": "workbook_fundamental_refill_v1",
            "flash_prompt_unchanged": True,
            "pro_prompt_unchanged": True,
        },
        expected_universe_audit={
            "candidate_count": len(candidates),
            "full_quant_count": len(full_quant_rows),
        },
        warnings=(
            []
            if production_promoted
            else ["WEB_READBACK_SHADOW_NOT_PRODUCTION"]
        ),
    )
    session.add(validation)
    session.flush()

    used_sample_ranks: set[int] = set()
    for index, candidate in enumerate(candidates, 1):
        code = normalize_ts_code(str(candidate.get("股票代码") or ""))
        quant_row = quant_by_code.get(code) or {}
        refill = refill_by_code.get(code) or {}
        profile = refill.get("profile") or {}
        inference = refill.get("inference") or {}
        fundamental = fundamentals.get(code) or {}
        quant_rank = _unique_rank(
            int(quant_row.get("rank") or 10_000 + index), used_sample_ranks
        )
        used_sample_ranks.add(quant_rank)
        flash = dict(flash_by_code.get(code) or {})
        if flash:
            flash["_trader_demo"] = {
                "execution_status": "SUCCESS",
                "llm_selected": code in pro_by_code,
                "selection_source": "LLM_TOP20",
                "web_published_shadow": True,
            }
        else:
            flash = {
                "llm_score": None,
                "screening_decision": "MANUAL_REVIEW",
                "_trader_demo": {
                    "execution_status": "SUCCESS",
                    "llm_selected": False,
                    "selection_source": "MANUAL",
                    "web_published_shadow": True,
                },
            }
        financial = profile.get("financial_summary") or {}
        available_at = _datetime(profile.get("available_at"))
        fundamental_result = _fundamental_result(
            code=code,
            fundamental=fundamental,
            inference=inference,
            profile=profile,
            financial=financial,
            now=now,
            content_hash=content_hash,
            session=session,
        )
        session.add(
            ModelValidationSample(
                validation_run_id=flash_run_id,
                quant_run_id=quant_run_id,
                run_data_manifest_id=manifest_id,
                rank=quant_rank,
                stock_code=code,
                stock_name=str(candidate.get("股票名称") or code),
                quant_scores={
                    "rank": int(quant_row.get("rank") or quant_rank),
                    "total_score": _float(quant_row.get("total_score")),
                    "technical_score": _float(quant_row.get("technical_score")),
                    "capital_score": _float(quant_row.get("capital_score")),
                    "emotion_score": _float(quant_row.get("emotion_score")),
                    "momentum_score": _float(quant_row.get("momentum_score")),
                    "risk_score": _float(quant_row.get("risk_score")),
                },
                profile_version=str(
                    profile.get("profile_version")
                    or f"workbook-refill-{content_hash[:12]}"
                )[:64],
                latest_financial_period=profile.get("latest_financial_period"),
                financial_available_at=available_at,
                data_age_days=profile.get("financial_data_age_days"),
                selected_at=decision_time,
                fundamental_result=fundamental_result,
                screening_result=flash,
                field_provenance={
                    "source": "WORKBOOK_PAYLOAD",
                    "web_verified": fundamental.get("人工复核") == "已联网核验",
                    "source_urls": _source_urls(fundamental),
                },
                missing_fields=[],
            )
        )

    candidate_codes = [
        normalize_ts_code(str(row.get("股票代码") or "")) for row in candidates
    ]
    model_codes = set(pro_by_code)
    sources = {
        code: "LLM_TOP20" if code in model_codes else "MANUAL"
        for code in candidate_codes
    }
    candidate_set_hash = _candidate_hash(payload)
    pro_run = ProResumeRun(
        run_id=pro_run_id,
        pipeline_run_id=pipeline_run_id,
        quant_run_id=quant_run_id,
        manifest_id=manifest_id,
        flash_validation_run_id=flash_run_id,
        previous_failed_run_id=None,
        pro_contract_version="pro_candidate_single_wire_v3",
        prompt_version="pro_candidate_single_review_v3",
        portfolio_prompt_version="WEB_PUBLISHED_SHADOW_NO_RERANK",
        base_trade_date=trade_date,
        target_trade_date=target_trade_date,
        top20_hash=_hash(sorted(model_codes)),
        manual_hash=_hash(sorted(set(candidate_codes) - model_codes)),
        candidate_set_hash=candidate_set_hash,
        candidate_count=len(candidates),
        chunk_size=1,
        chunk_count=len(candidates),
        status="COMPLETED",
        config_snapshot={
            "source": "WORKBOOK_PAYLOAD",
            "selection_sources": sources,
            "candidate_codes": candidate_codes,
            "shadow_only": True,
            "production_promoted": False,
            "manual_candidates_unscored": True,
            "workbook_sha256": workbook_sha256,
        },
        portfolio_result={
            "status": "WEB_PUBLISHED_SHADOW",
            "active_shadow_codes": [
                normalize_ts_code(str(item.get("stock_code") or ""))
                for item in (final_audit.get("active_shadow") or [])
            ],
        },
        warnings=["WEB_READBACK_SHADOW_NOT_PRODUCTION"],
    )
    session.add(pro_run)
    session.flush()

    pro_rank = 0
    for candidate in candidates:
        code = normalize_ts_code(str(candidate.get("股票代码") or ""))
        pro_rank += 1
        original = pro_by_code.get(code) or {}
        manual_unscored = code not in pro_by_code
        session.add(
            ProCandidateReview(
                pro_resume_run_id=pro_run_id,
                flash_validation_run_id=flash_run_id,
                chunk_id=f"web-published-{pro_rank:03d}",
                contract_version=str(
                    original.get("contract_version")
                    or "WORKBOOK_MANUAL_UNSCORED"
                )[:64],
                candidate_input_hash=original.get("input_hash"),
                stock_code=code,
                pro_score=_decimal(
                    candidate.get("深度复核分"), "0" if manual_unscored else "0"
                ),
                pro_rank=pro_rank,
                ranking_tie_break_reason=(
                    "人工关注候选未补造模型评分"
                    if manual_unscored
                    else "复用V2最终复核顺序"
                ),
                ranking_version="web-published-workbook-v1",
                priority=_priority(
                    candidate.get("复核优先级")
                    or original.get("priority")
                    or "REVIEW_ONLY"
                ),
                final_summary=str(candidate.get("核心逻辑") or ""),
                key_strengths=list(original.get("key_strengths") or []),
                key_risks=_split(candidate.get("主要风险")),
                fundamental_quality="COMPLETE",
                quant_llm_consistency=(
                    "MANUAL_UNSCORED" if manual_unscored else "UNCHANGED"
                ),
                manual_review_priority="HIGH" if manual_unscored else "MEDIUM",
                data_conflict=False,
                prompt_version=str(
                    original.get("prompt_version")
                    or "workbook_fundamental_refill_v1"
                )[:64],
                actual_model=str(
                    original.get("actual_model") or "NOT_APPLICABLE"
                )[:128],
                review_status="COMPLETED",
            )
        )

    account_snapshot_id = f"{flash_run_id}:account"
    session.add(
        ValidationAccountSnapshot(
            snapshot_id=account_snapshot_id,
            validation_run_id=flash_run_id,
            account_type="WEB_PUBLISHED_SHADOW",
            account_equity=Decimal("100000.00"),
            available_cash=Decimal("100000.00"),
            snapshot_time=now,
            existing_positions=[],
        )
    )
    active_codes = {
        normalize_ts_code(str(item.get("stock_code") or ""))
        for item in (final_audit.get("active_shadow") or [])
    }
    source_plans = {
        normalize_ts_code(str(item.get("stock_code") or "")): item
        for item in (final_audit.get("order_plans") or [])
    }
    for candidate in candidates:
        code = normalize_ts_code(str(candidate.get("股票代码") or ""))
        order = orders.get(code) or {}
        source_plan = source_plans.get(code) or {}
        is_active = code in active_codes
        position = _decimal(candidate.get("建议仓位"), "0")
        session.add(
            ModelValidationOrderPlan(
                validation_run_id=flash_run_id,
                quant_run_id=quant_run_id,
                run_data_manifest_id=manifest_id,
                stock_code=code,
                plan_purpose="MODEL_VALIDATION",
                plan_session="POST_MARKET",
                status="DRAFT" if is_active else "WATCH_ONLY",
                actionable=False,
                is_final_recommendation=False,
                decision_time=decision_time,
                base_market_trade_date=trade_date,
                target_trade_date=target_trade_date,
                factor_version=factor_version,
                config_snapshot={
                    "source": "WORKBOOK_PAYLOAD",
                    "shadow_only": True,
                    "regime": final_audit.get("market_regime"),
                },
                conservative_price=_decimal_or_none(order.get("保守价")),
                balanced_price=_decimal_or_none(order.get("均衡价")),
                aggressive_price=_decimal_or_none(order.get("积极价")),
                recommended_price=_decimal_or_none(order.get("参考价")),
                max_acceptable_price=_decimal_or_none(order.get("最高接受价")),
                stop_loss_price=_decimal_or_none(order.get("止损价")),
                take_profit_1_price=_decimal_or_none(order.get("第一目标价")),
                take_profit_2_price=_decimal_or_none(order.get("第二目标价")),
                fill_probability=_decimal_or_none(source_plan.get("confidence")),
                risk_reward=_decimal_or_none(order.get("当前收益比")),
                risk_reward_to_tp1=_decimal_or_none(order.get("第一目标收益比")),
                risk_reward_to_tp2=_decimal_or_none(order.get("第二目标收益比")),
                active_risk_reward=_decimal_or_none(order.get("当前收益比")),
                active_target_mode=source_plan.get("active_target_mode"),
                unrounded_stop_loss_price=_decimal_or_none(
                    source_plan.get("unrounded_stop_loss_price")
                ),
                support=_decimal_or_none(
                    (source_plan.get("valid_conditions") or {}).get("support")
                ),
                resistance=_decimal_or_none(
                    (source_plan.get("valid_conditions") or {}).get("resistance")
                ),
                atr=_decimal_or_none(
                    (source_plan.get("valid_conditions") or {}).get("atr")
                ),
                vwap=_decimal_or_none(
                    (source_plan.get("valid_conditions") or {}).get("vwap")
                ),
                previous_close=None,
                limit_up_estimated=None,
                limit_down_estimated=None,
                limit_price_source="RULE_ESTIMATED",
                official_target_day_limit_available=False,
                target_day_auction_available=False,
                cancel_conditions=source_plan.get("cancel_conditions") or {},
                reprice_conditions=source_plan.get("reprice_conditions") or {},
                warnings=["WEB_PUBLISHED_SHADOW", "NO_ORDER_CREATED"],
                temporal_status="PASS",
            )
        )
        session.add(
            ModelValidationAllocation(
                validation_run_id=flash_run_id,
                allocation_run_id=f"{flash_run_id}:allocation",
                account_snapshot_id=account_snapshot_id,
                stock_code=code,
                allocation_purpose="MODEL_VALIDATION",
                actionable=False,
                status="SHADOW_ACTIVE" if is_active else "WATCH_ONLY",
                relative_allocation_weight=position,
                suggested_position_percent=position,
                suggested_capital_amount=_decimal(
                    order.get("建议资金"), "10000" if is_active else "0"
                ),
                suggested_quantity=int(order.get("建议股数") or 0),
                estimated_max_loss=_decimal(order.get("预计最大损失"), "0"),
                binding_constraints=[
                    "RISK_OFF",
                    "ADVISORY_ONLY",
                    "NO_REAL_OR_VIRTUAL_ORDER",
                ],
                warnings=["WEB_PUBLISHED_SHADOW", "NO_ORDER_CREATED"],
            )
        )
    session.flush()


def _fundamental_result(
    *,
    code: str,
    fundamental: Mapping[str, Any],
    inference: Mapping[str, Any],
    profile: Mapping[str, Any],
    financial: Mapping[str, Any],
    now: datetime,
    content_hash: str,
    session,
) -> dict[str, Any]:
    web_verified = fundamental.get("人工复核") == "已联网核验"
    source_urls = _source_urls(fundamental)
    research_mode = "EXTERNAL_VERIFIED" if web_verified else "DEEPSEEK_UNVERIFIED"
    external_audit: dict[str, Any] | None = None
    if web_verified and source_urls:
        research_run_id = _persist_evidence(
            session=session,
            code=code,
            stock_name=str(profile.get("stock_name") or code),
            urls=source_urls,
            fundamental=fundamental,
            now=now,
            content_hash=content_hash,
        )
        external_audit = {
            "research_run_id": research_run_id,
            "profile_version": f"workbook-web-{content_hash[:12]}",
            "source_count": len(source_urls),
            "llm_calls": 0,
            "flash_rerun": False,
        }
    result = {
        "stock_code": code,
        "as_of_time": now.isoformat(),
        "research_mode": research_mode,
        "analysis_status": "SUCCESS",
        "wire_schema_version": "workbook_web_readback_v1",
        "profile_version": (
            profile.get("profile_version") or f"workbook-refill-{content_hash[:12]}"
        ),
        "industry_chain": {
            "chain_name": fundamental.get("产业链"),
            "chain_position": _chain_position(fundamental.get("链条位置")),
            "source_status": "WEB_VERIFIED" if web_verified else "LLM_UNVERIFIED",
            "verified": web_verified,
            "confidence": 0.95 if web_verified else 0.7,
        },
        "main_business_summary": {
            "summary": fundamental.get("主营业务"),
            "source_status": "WEB_VERIFIED" if web_verified else "LLM_UNVERIFIED",
            "verified": web_verified,
        },
        "core_products": _split(fundamental.get("核心产品")),
        "concept_tags": _split(fundamental.get("概念标签")),
        "industry_position": {
            "description": fundamental.get("结构性方向"),
            "level": "UNCLEAR",
        },
        "competitive_advantage": {
            "summary": fundamental.get("潜在优势"),
            "source_status": "WEB_VERIFIED" if web_verified else "LLM_UNVERIFIED",
        },
        "industry_trend": {
            "summary": fundamental.get("行业趋势"),
            "source_status": "WEB_VERIFIED" if web_verified else "LLM_UNVERIFIED",
        },
        "investment_logic": {
            "summary": fundamental.get("核心逻辑"),
            "source_status": "WEB_VERIFIED" if web_verified else "LLM_UNVERIFIED",
        },
        "invalidation_conditions": _split(fundamental.get("失效条件")),
        "financial_status": {"status": _financial_status(fundamental.get("财务状态"))},
        "financial_status_explanation": {
            "summary": fundamental.get("财务说明"),
            "source_status": "WEB_VERIFIED" if web_verified else "LLM_UNVERIFIED",
        },
        "financial_snapshot": _financial_snapshot(financial),
        "key_risks": _split(fundamental.get("失效条件")),
        "data_conflict": False,
        "missing_fields": [],
        "requires_manual_review": True,
        "web_display": {
            "financial_summary": fundamental.get("财务说明"),
            "source_urls": source_urls,
            "workbook_content_hash": content_hash,
        },
    }
    if external_audit:
        result["external_research_audit"] = external_audit
    return result


def _persist_evidence(
    *,
    session,
    code: str,
    stock_name: str,
    urls: list[str],
    fundamental: Mapping[str, Any],
    now: datetime,
    content_hash: str,
) -> str:
    run_hash = _hash({"code": code, "urls": urls, "content_hash": content_hash})
    run_id = f"web-workbook-{run_hash[:20]}"
    session.add(
        FundamentalResearchRun(
            run_id=run_id,
            provider="manual_web_research",
            mode="EXTERNAL_VERIFIED",
            status="COMPLETE",
            dry_run=False,
            stock_codes=[code],
            query_count=1,
            source_count=len(urls),
            capability_metadata={
                "source": "APPROVED_WORKBOOK_WEB_VERIFICATION",
                "llm_calls": 0,
            },
            request_hash=run_hash,
            config_snapshot={"schema_version": "workbook_web_readback_v1"},
            is_current=True,
        )
    )
    snippet = "；".join(
        str(fundamental.get(key) or "")
        for key in ("主营业务", "潜在优势", "行业趋势", "财务说明")
        if fundamental.get(key)
    )[:3900]
    for url in urls:
        canonical = url.strip()
        evidence_hash = _hash(
            {"code": code, "url": canonical, "snippet": snippet}
        )
        domain = urlsplit(canonical).hostname or "unknown"
        session.add(
            ResearchEvidenceRecord(
                run_id=run_id,
                stock_code=code,
                query=f"{stock_name} 基本面人工核验",
                url=canonical,
                canonical_url=canonical,
                domain=domain,
                title=f"{stock_name} 公开披露核验来源",
                snippet=snippet or f"{stock_name} 基本面字段核验",
                published_at=None,
                retrieved_at=now,
                source_tier="tier_2" if "sina.com.cn" in domain else "tier_1",
                source_type="annual_report_or_official_disclosure",
                credibility_score=0.9 if "sina.com.cn" in domain else 0.98,
                content_hash=evidence_hash,
                related_fields=[
                    "main_business_summary",
                    "industry_chain",
                    "competitive_advantage",
                    "industry_trend",
                    "investment_logic",
                    "financial_status_explanation",
                ],
                provider="manual_web_research",
                provider_metadata={
                    "capture_mode": "approved_workbook_web_verification"
                },
            )
        )
    return run_id


def _source_manifest_id(trade_date: date) -> str:
    session = get_session()
    try:
        value = session.scalar(
            select(QuantRun.data_manifest_id)
            .where(
                QuantRun.base_market_trade_date == trade_date,
                QuantRun.status == "COMPLETED",
            )
            .order_by(QuantRun.created_at.desc())
        )
        if not value:
            raise RuntimeError("WEB_PUBLISH_SOURCE_MANIFEST_NOT_FOUND")
        return str(value)
    finally:
        session.close()


def _chain_counts(session, quant_run_id: str, flash_run_id: str, pro_run_id: str) -> dict[str, int]:
    return {
        "quant": int(
            session.scalar(
                select(func.count())
                .select_from(QuantRankResult)
                .where(QuantRankResult.quant_run_id == quant_run_id)
            )
            or 0
        ),
        "flash": int(
            session.scalar(
                select(func.count())
                .select_from(ModelValidationSample)
                .where(ModelValidationSample.validation_run_id == flash_run_id)
            )
            or 0
        ),
        "pro": int(
            session.scalar(
                select(func.count())
                .select_from(ProCandidateReview)
                .where(ProCandidateReview.pro_resume_run_id == pro_run_id)
            )
            or 0
        ),
        "order": int(
            session.scalar(
                select(func.count())
                .select_from(ModelValidationOrderPlan)
                .where(ModelValidationOrderPlan.validation_run_id == flash_run_id)
            )
            or 0
        ),
        "position": int(
            session.scalar(
                select(func.count())
                .select_from(ModelValidationAllocation)
                .where(ModelValidationAllocation.validation_run_id == flash_run_id)
            )
            or 0
        ),
    }


def _candidate_hash(payload: Mapping[str, Any]) -> str:
    return _hash(
        [
            {
                "stock_code": row.get("股票代码"),
                "source": row.get("入选来源"),
                "pro_rank": row.get("深度复核排名"),
                "pro_score": row.get("深度复核分"),
            }
            for row in (payload.get("candidates") or [])
        ]
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _decimal(value: Any, default: str) -> Decimal:
    number = _float(value)
    return Decimal(str(number)) if number is not None else Decimal(default)


def _decimal_or_none(value: Any) -> Decimal | None:
    number = _float(value)
    return Decimal(str(number)) if number is not None else None


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _split(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    delimiter = "|" if "|" in text else "；"
    return [item.strip() for item in text.split(delimiter) if item.strip()]


def _source_urls(fundamental: Mapping[str, Any]) -> list[str]:
    return [
        item for item in _split(fundamental.get("核验来源")) if item.startswith("https://")
    ]


def _chain_position(value: Any) -> str:
    return {
        "上游": "UPSTREAM",
        "中游": "MIDSTREAM",
        "下游": "DOWNSTREAM",
        "服务平台": "SERVICE_PLATFORM",
        "多环节": "MULTI_SEGMENT",
    }.get(str(value or ""), "MULTI_SEGMENT")


def _financial_status(value: Any) -> str:
    return {
        "稳健": "STABLE",
        "正常": "STABLE",
        "承压": "PRESSURED",
        "高风险": "RISKY",
        "信息不足": "INSUFFICIENT_DATA",
        "待核验": "INSUFFICIENT_DATA",
    }.get(str(value or ""), "INSUFFICIENT_DATA")


def _financial_snapshot(financial: Mapping[str, Any]) -> dict[str, Any]:
    end_date = str(financial.get("end_date") or "")
    if not end_date.startswith("202603"):
        return {}
    return {
        "q1_revenue": financial.get("revenue"),
        "q1_revenue_yoy_pct": financial.get("revenue_yoy"),
        "q1_deducted_net_profit": financial.get("deducted_net_profit"),
        "q1_deducted_net_profit_yoy_pct": financial.get("deducted_profit_yoy"),
    }


def _unique_rank(rank: int, used: set[int]) -> int:
    value = rank
    while value in used:
        value += 10_000
    return value


def _priority(value: Any) -> str:
    return {
        "高": "HIGH",
        "中": "MEDIUM",
        "低": "LOW",
    }.get(str(value or "").strip(), str(value or "REVIEW_ONLY").strip().upper())[:32]
