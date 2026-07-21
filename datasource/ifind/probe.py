from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from datasource.ifind.capability import IFindSampleSelector, untested_capabilities
from datasource.ifind.errors import sanitize_error
from datasource.ifind.http import IFindHttpAuthManager, IFindHttpClient
from datasource.ifind.http.errors import IFindHttpError
from datasource.ifind.http.normalizer import normalize_probe_response
from datasource.ifind.schemas import (
    CapabilityResult,
    CapabilityStatus,
    IFindAuditReport,
    IFindErrorCategory,
    ProbeCallSummary,
    ProductionRecommendation,
)
from datasource.ifind.session import IFindSessionManager
from datasource.ifind.transport import IFindCredentialStatus, IFindTransportMode, select_transport


class ProbeCallLimitReached(RuntimeError):
    pass


class SerialProbeLimiter:
    def __init__(self, configured_maximum: int, hard_maximum: int, min_interval_ms: int, sleep: Callable[[float], None] = time.sleep) -> None:
        self.configured_maximum = min(max(1, configured_maximum), hard_maximum)
        self.hard_maximum = min(max(1, hard_maximum), 50)
        self.min_interval_ms = max(1000, min_interval_ms)
        self.sleep = sleep
        self.call_count = 0
        self._last_call_at: float | None = None

    def call(self, operation: Callable[[], Any]) -> tuple[Any, int]:
        if self.call_count >= self.configured_maximum or self.call_count >= self.hard_maximum:
            raise ProbeCallLimitReached("CALL_LIMIT_REACHED")
        now = time.monotonic()
        if self._last_call_at is not None:
            remaining = self.min_interval_ms / 1000 - (now - self._last_call_at)
            if remaining > 0:
                self.sleep(remaining)
        started = time.perf_counter()
        result = operation()
        latency = round((time.perf_counter() - started) * 1000)
        self.call_count += 1
        self._last_call_at = time.monotonic()
        return result, latency


class IFindCapabilityProbe:
    def __init__(self, session, config: dict[str, Any], *, output_dir: Path, session_manager: IFindSessionManager | None = None, cache_root: Path | None = None) -> None:
        self.db_session = session
        self.config = _validated_config(config)
        self.output_dir = output_dir
        self.manager = session_manager or IFindSessionManager(self.config)
        self.selector = IFindSampleSelector(session, self.config, cache_root=cache_root)

    def run(
        self,
        *,
        real_probe: bool = False,
        categories: set[str] | None = None,
        stop_on_login_failure: bool = False,
        transport: str = "auto",
    ) -> IFindAuditReport:
        environment = self.manager.inspect_environment()
        if self.config.get("skip_packaging_audit"):
            environment = environment.model_copy(update={"packaging_feasibility": "NOT_AUDITED"})
        samples = self.selector.select()
        selected_transport, credential_status = select_transport(transport)
        configured = credential_status != IFindCredentialStatus.NOT_CONFIGURED
        scheduler_enabled = _scheduler_enabled()
        real_trading = os.getenv("ENABLE_REAL_TRADING", "false").strip().lower() not in {"", "0", "false", "no", "off"}
        gate = _real_probe_gate(real_probe, configured, scheduler_enabled, real_trading, selected_transport)
        sdk_installed = environment.sdk_status == "AVAILABLE"
        problems = []
        if selected_transport == IFindTransportMode.SDK and not sdk_installed:
            problems.append("SDK_NOT_INSTALLED: real probe stopped before authentication")
        if gate not in {"PASS", "DRY_RUN"}:
            problems.append(gate)
        capabilities = untested_capabilities(
            samples, configured=configured, sdk_installed=sdk_installed, transport=selected_transport.value,
        )
        if categories:
            capabilities = [item for item in capabilities if item.category in categories]

        login_status = "NOT_CONFIGURED" if not configured else "UNKNOWN"
        auth_calls = 0
        auth_details: dict[str, Any] = {}
        http_client: IFindHttpClient | None = None
        mode = "DRY_RUN"
        overall = "DRY_RUN"
        if real_probe and gate == "PASS" and selected_transport == IFindTransportMode.HTTP:
            mode = "REAL_HTTP_PROBE"
            auth = IFindHttpAuthManager(
                base_url=str(self.config.get("http_base_url") or "https://quantapi.51ifind.com/api/v1"),
                timeout_seconds=float(self.config["timeout_seconds"]),
            )
            try:
                if auth.has_refresh_token:
                    auth_result = auth.refresh_access_token()
                    login_status = "AUTH_SUCCESS"
                elif auth.has_access_token:
                    auth_result = auth.last_result
                    login_status = "ACCESS_TOKEN_UNVERIFIED"
                else:
                    raise RuntimeError("HTTP credential is not configured")
                auth_calls = auth.auth_calls
                auth_details = {
                    "auth_status": auth_result.status,
                    "auth_source": auth_result.source,
                    "expires_at": auth_result.expires_at.isoformat() if auth_result.expires_at else None,
                    "auth_latency_ms": auth_result.latency_ms,
                    "auth_error_category": auth_result.error_category,
                    "auth_response_schema_hash": auth_result.response_schema_hash,
                }
                http_client = IFindHttpClient(
                    auth, timeout_seconds=float(self.config["timeout_seconds"]),
                    maximum_calls=int(self.config["max_total_calls"]), interval_ms=int(self.config["min_interval_ms"]),
                )
                capabilities = self._run_http_probes(http_client, samples, categories)
                overall = "HTTP_PROBE_COMPLETED"
            except IFindHttpError as exc:
                auth_calls = auth.auth_calls
                login_status = "AUTH_FAILED"
                overall = "HTTP_AUTH_FAILED"
                auth_details = {
                    "auth_status": auth.last_result.status,
                    "auth_source": auth.last_result.source,
                    "expires_at": None,
                    "auth_latency_ms": auth.last_result.latency_ms,
                    "auth_error_category": exc.category.value,
                    "auth_response_schema_hash": auth.last_result.response_schema_hash,
                }
                problems.append(exc.category.value)
                if stop_on_login_failure:
                    capabilities = capabilities
            except Exception as exc:
                login_status = "AUTH_FAILED"
                overall = "HTTP_AUTH_FAILED"
                problems.append(sanitize_error(exc, _secret_values()))
        elif real_probe and gate == "PASS" and selected_transport == IFindTransportMode.SDK and sdk_installed:
            mode = "REAL_SDK_PROBE"
            try:
                with self.manager:
                    login_status = self.manager.login()
                overall = "LOGIN_SUCCESS_NO_CAPABILITY_MAPPING"
            except Exception as exc:
                login_status = "LOGIN_FAILED"
                overall = "LOGIN_FAILED"
                problems.append(sanitize_error(exc, _secret_values()))
        elif real_probe and gate == "PASS" and selected_transport == IFindTransportMode.SDK and not sdk_installed:
            mode, overall = "REAL_PROBE_BLOCKED", "SDK_NOT_INSTALLED"
        elif real_probe:
            mode, overall = "REAL_PROBE_BLOCKED", gate

        actual_calls = int(http_client.call_count if http_client else self.manager.call_count)
        success_count = int(http_client.success_count if http_client else self.manager.success_count)
        failed_count = int(http_client.failed_count if http_client else self.manager.failed_count)
        unauthorized_count = int(http_client.unauthorized_count if http_client else 0)
        report = IFindAuditReport(
            audit_id=f"ifind-audit-{uuid.uuid4().hex[:20]}", audited_at=datetime.now(timezone.utc), mode=mode,
            overall_status=overall, environment=environment,
            authentication={
                "credential_status": credential_status.value, "login_status": login_status,
                "authentication_mode": "HTTP_TOKEN" if selected_transport == IFindTransportMode.HTTP else _authentication_mode(self.config),
                "transport": selected_transport.value, "auth_calls": auth_calls,
                "sdk_credential_status": "CONFIGURED" if _credentials_configured(self.config) else "NOT_CONFIGURED",
                "http_credential_status": _http_credential_status(), **auth_details,
            },
            trial_account={"status": "UNKNOWN", "expiration": None, "reason": "SDK_NOT_INSTALLED" if not sdk_installed else "NOT_RETURNED"},
            quota_summary={"visibility": "NOT_AVAILABLE", "qps_limit": None, "weekly_limit": None, "units_consumed": None},
            samples=samples, capabilities=capabilities,
            call_summary=ProbeCallSummary(
                configured_maximum=int(self.config["max_total_calls"]), hard_maximum=int(self.config["hard_max_total_calls"]),
                actual_calls=actual_calls, successful_calls=success_count, empty_valid_calls=sum(item.status == CapabilityStatus.EMPTY_VALID for item in capabilities),
                unauthorized_calls=unauthorized_count, failed_calls=failed_count,
                call_pacing_ms=int(self.config["min_interval_ms"]), sequential=True,
                limit_status="CALL_LIMIT_REACHED" if actual_calls >= int(self.config["hard_max_total_calls"]) else "WITHIN_LIMIT",
            ),
            batch_estimates=_batch_estimates(), packaging_summary={
                "feasibility": environment.packaging_feasibility, "requires_ifind_client": "UNKNOWN",
                "requires_native_dll": bool(environment.native_dll_count or environment.native_so_count), "pyinstaller_distribution_allowed": "NOT_CONFIRMED",
                "target_machine_component_install": "UNKNOWN", "license_confirmed": False,
            },
            business_table_writes=0, production_route_changed=False, scheduler_enabled=scheduler_enabled,
            real_trading_enabled=real_trading, quant_or_llm_called=False, raw_response_saved=False, problems=problems,
        )
        return self._write_reports(report)

    def _run_http_probes(self, client: IFindHttpClient, samples, categories: set[str] | None) -> list[CapabilityResult]:
        requested = categories or {"INDEX", "REALTIME", "ORDER_BOOK", "MINUTE", "AUCTION", "INDUSTRY", "CONCEPT"}
        specs = _http_probe_specs(samples, self.config)
        results: list[CapabilityResult] = []
        covered: set[str] = set()
        for spec in specs:
            if spec["category"] not in requested:
                continue
            covered.add(spec["category"])
            results.append(_execute_http_probe(client, spec))
        remaining = untested_capabilities(
            samples, configured=True, sdk_installed=True, transport="HTTP",
        )
        results.extend(item for item in remaining if item.category in requested and item.category not in covered)
        return results

    def _write_reports(self, report: IFindAuditReport) -> IFindAuditReport:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = report.audited_at.strftime("%Y%m%d_%H%M%S")
        json_path = self.output_dir / f"ifind_capability_audit_{stamp}.json"
        summary_path = self.output_dir / f"ifind_probe_call_summary_{stamp}.json"
        report = report.model_copy(update={
            "report_path": _safe_report_path(json_path), "call_summary_path": _safe_report_path(summary_path),
        })
        payload = report.model_dump(mode="json")
        _assert_sanitized(payload)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summary_payload = {
            "audit_id": report.audit_id, "mode": report.mode,
            "call_summary": report.call_summary.model_dump(mode="json"),
            "capability_status_counts": _status_counts(report), "raw_response_saved": False,
        }
        _assert_sanitized(summary_payload)
        summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        docs_path = Path("docs/IFIND_TRIAL_CAPABILITY_AUDIT.md")
        docs_path.parent.mkdir(parents=True, exist_ok=True)
        docs_path.write_text(_markdown_v2(report), encoding="utf-8")
        return report


def load_latest_audit(output_dir: Path) -> dict[str, Any] | None:
    paths = sorted(output_dir.glob("ifind_capability_audit_*.json"), reverse=True) if output_dir.exists() else []
    if not paths:
        return None
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    _assert_sanitized(payload)
    return payload


def _validated_config(config: dict[str, Any]) -> dict[str, Any]:
    values = dict(config)
    hard = int(values.get("hard_max_total_calls", 50))
    maximum = int(values.get("max_total_calls", 30))
    if hard > 50 or hard < 1 or maximum < 1 or maximum > hard:
        raise ValueError("IFIND_PROBE_CALL_LIMIT_INVALID")
    if int(values.get("min_interval_ms", 1000)) < 1000:
        raise ValueError("IFIND_PROBE_INTERVAL_TOO_SHORT")
    if bool(values.get("save_raw_response")) or bool(values.get("persist_business_data")):
        raise ValueError("IFIND_PROBE_PERSISTENCE_MUST_REMAIN_DISABLED")
    values.update({"hard_max_total_calls": hard, "max_total_calls": maximum, "min_interval_ms": int(values.get("min_interval_ms", 1000))})
    return values


def _real_probe_gate(
    real_probe: bool,
    configured: bool,
    scheduler_enabled: bool,
    real_trading: bool,
    transport: IFindTransportMode,
) -> str:
    if not real_probe:
        return "DRY_RUN"
    if os.getenv("RUN_REAL_IFIND_PROBE", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return "RUN_REAL_IFIND_PROBE_NOT_ENABLED"
    if not configured:
        return "IFIND_CREDENTIAL_NOT_CONFIGURED"
    if transport == IFindTransportMode.HTTP and os.getenv("IFIND_HTTP_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return "IFIND_HTTP_NOT_ENABLED"
    if real_trading:
        return "REAL_TRADING_MUST_BE_DISABLED"
    if scheduler_enabled:
        return "SCHEDULER_MUST_BE_DISABLED"
    return "PASS"


def _http_credential_status() -> str:
    if os.getenv("IFIND_REFRESH_TOKEN", "").strip():
        return "CONFIGURED"
    if os.getenv("IFIND_ACCESS_TOKEN", "").strip():
        return "ACCESS_TOKEN_ONLY_NO_REFRESH"
    return "NOT_CONFIGURED"


def _credentials_configured(config: dict[str, Any]) -> bool:
    mapping = dict(config.get("login_arguments") or {})
    return bool(mapping) and all(os.getenv(str(env_name), "").strip() for env_name in mapping.values())


def _authentication_mode(config: dict[str, Any]) -> str:
    arguments = set((config.get("login_arguments") or {}).keys())
    if arguments == {"username", "password"}:
        return "USERNAME_PASSWORD"
    if arguments == {"access_token"}:
        return "ACCESS_TOKEN"
    return "UNCONFIRMED"


def _scheduler_enabled() -> bool:
    try:
        import yaml
        payload = yaml.safe_load(Path("config/schedule.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return True
    return any(bool(value.get("enabled")) for value in (payload.get("schedule") or {}).values() if isinstance(value, dict))


def _http_probe_specs(samples, config: dict[str, Any]) -> list[dict[str, Any]]:
    stocks = [item.canonical_code for item in samples if item.board != "INDEX"]
    indices = [item.canonical_code for item in samples if item.board == "INDEX"]
    latest_local = max((item.local_latest_trade_date or "" for item in samples), default="")
    try:
        trade_date = datetime.strptime(latest_local, "%Y%m%d").date() if latest_local else datetime.now(ZoneInfo("Asia/Shanghai")).date()
    except ValueError:
        trade_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    start_date = trade_date - timedelta(days=max(1, int(config.get("daily_history_days", 5))) + 3)
    minute_start = f"{trade_date.isoformat()} 14:50:00"
    minute_end = f"{trade_date.isoformat()} 15:00:00"
    specs: list[dict[str, Any]] = []
    if indices:
        specs.extend([
            {
                "category": "INDEX", "name": "Index daily", "endpoint": "cmd_history_quotation",
                "codes": indices, "recommendation": ProductionRecommendation.P0_INTEGRATE,
                "payload": {
                    "codes": ",".join(indices), "indicators": "open,high,low,close,volume,amount",
                    "startdate": start_date.isoformat(), "enddate": trade_date.isoformat(),
                    "functionpara": {"Interval": "D"},
                },
            },
            {
                "category": "INDEX", "name": "Index realtime", "endpoint": "real_time_quotation",
                "codes": indices, "recommendation": ProductionRecommendation.P0_INTEGRATE,
                "payload": {
                    "codes": ",".join(indices), "indicators": "open,high,low,latest,volume,amount",
                    "functionpara": {},
                },
            },
        ])
    if stocks:
        specs.extend([
            {
                "category": "REALTIME", "name": "Stock realtime", "endpoint": "real_time_quotation",
                "codes": stocks, "recommendation": ProductionRecommendation.NEED_DATAFEED_PRODUCT,
                "payload": {
                    "codes": ",".join(stocks), "indicators": "open,high,low,latest,volume,amount",
                    "functionpara": {},
                },
            },
            {
                "category": "MINUTE", "name": "Minute bars", "endpoint": "high_frequency",
                "codes": stocks[:1], "recommendation": ProductionRecommendation.NEED_DATAFEED_PRODUCT,
                "payload": {
                    "codes": stocks[0], "indicators": "open,high,low,close,volume,amount",
                    "starttime": minute_start, "endtime": minute_end,
                    "functionpara": {"Interval": "1"},
                },
            },
        ])
    return specs


def _execute_http_probe(client: IFindHttpClient, spec: dict[str, Any]) -> CapabilityResult:
    observed_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    base = {
        "category": spec["category"], "capability_name": spec["name"], "configured": True,
        "sample_codes": list(spec["codes"]), "request_scope": f"HTTP_{spec['endpoint']}",
        "production_recommendation": spec["recommendation"], "endpoint": spec["endpoint"],
        "local_observation_time": observed_at.isoformat(),
        "market_session": "CLOSED" if observed_at.weekday() < 5 and observed_at.hour >= 15 else "OPEN_OR_UNKNOWN",
    }
    try:
        response = client.post(spec["endpoint"], dict(spec["payload"]))
        normalized = normalize_probe_response(response.payload, observed_at=observed_at)
        status = CapabilityStatus.AVAILABLE if normalized.row_count else CapabilityStatus.EMPTY_VALID
        return CapabilityResult(
            **base, status=status, authorized=True, call_count=1,
            row_count=normalized.row_count, column_count=len(normalized.returned_fields),
            returned_fields=list(normalized.returned_fields), latest_data_time=normalized.latest_data_time,
            provider_server_time=normalized.provider_timestamp, observed_latency_ms=response.latency_ms,
            tested_batch_size=len(spec["codes"]), batch_supported=len(spec["codes"]) > 1,
            response_schema_hash=response.schema_hash, data_status=normalized.data_status,
            error_category=IFindErrorCategory.EMPTY_VALID_RESULT if status == CapabilityStatus.EMPTY_VALID else None,
            notes=[normalized.data_status],
        )
    except IFindHttpError as exc:
        try:
            category = IFindErrorCategory(exc.category.value)
        except ValueError:
            category = IFindErrorCategory.PROVIDER_ERROR
        denied = exc.category.value in {
            "NOT_AUTHORIZED", "ACCESS_TOKEN_INVALID", "ACCESS_TOKEN_EXPIRED", "ACCOUNT_EXPIRED",
        }
        return CapabilityResult(
            **base, status=CapabilityStatus.NOT_AUTHORIZED if denied else CapabilityStatus.ERROR,
            authorized=False if denied else None, call_count=1, error_category=category,
            sanitized_error=exc.category.value, data_status="TIME_UNKNOWN",
        )


def _batch_estimates() -> list[dict[str, Any]]:
    return [
        {"scenario": "POST_MARKET_FULL_A", "capability": "DAILY_MARKET", "status": "ESTIMATE_NOT_ACTUAL_USAGE", "stock_count": 5000, "tested_code_count": 0, "tested_indicator_count": 0, "returned_rows": 0, "latency_ms": None, "request_batches": None, "estimated_cells": None, "reason": "SDK batch and quota limits are unknown"},
        *[{"scenario": f"INTRADAY_POOL_{count}", "capability": "REALTIME", "status": "ESTIMATE_NOT_ACTUAL_USAGE", "stock_count": count, "tested_code_count": 0, "tested_indicator_count": 0, "returned_rows": 0, "latency_ms": None, "requests_per_minute": None, "requests_per_hour": None, "estimated_cells": None, "reason": "Realtime batch limit and cell accounting are unknown"} for count in (20, 50, 100)],
        {"scenario": "INDEX_AND_SECTOR", "capability": "INDEX_AND_SECTOR", "status": "ESTIMATE_NOT_ACTUAL_USAGE", "instrument_count": None, "tested_code_count": 0, "tested_indicator_count": 0, "returned_rows": 0, "latency_ms": None, "requests_per_minute": None, "estimated_cells": None, "reason": "Index and sector entitlement is not tested"},
    ]


def _markdown(report: IFindAuditReport) -> str:
    rows = "\n".join(f"| {item.category} | {item.capability_name} | {item.status} | {item.error_category or '-'} | {item.production_recommendation} |" for item in report.capabilities)
    samples = "\n".join(
        f"| {item.canonical_code} | {item.market} | {item.board} | {item.local_latest_trade_date or '-'} | {item.source} |"
        for item in report.samples
    )
    batches = "\n".join(
        f"| {item['scenario']} | {item['capability']} | {item['tested_code_count']} | {item['tested_indicator_count']} | {item['returned_rows']} | {item['latency_ms'] if item['latency_ms'] is not None else 'N/A'} | {item['estimated_cells'] if item['estimated_cells'] is not None else 'UNKNOWN'} |"
        for item in report.batch_estimates
    )
    problems = "\n".join(f"- {item}" for item in report.problems) or "- 无"
    return f"""# iFinD 试用能力审计

审计时间：{report.audited_at.isoformat()}

## 结论

- 模式：{report.mode}
- 状态：{report.overall_status}
- SDK：{report.environment.sdk_status}
- 登录：{report.authentication['login_status']}
- 外部调用：{report.call_summary.actual_calls}
- 正式业务表写入：{report.business_table_writes}
- 生产路由变更：{report.production_route_changed}
- 打包可行性：{report.packaging_summary['feasibility']}

## 认证、账号与配额

- 凭据状态：{report.authentication['credential_status']}
- 认证方式：{report.authentication['authentication_mode']}
- 登录状态：{report.authentication['login_status']}
- 试用账号状态：{report.trial_account['status']}
- 到期时间：{report.trial_account['expiration'] or 'UNKNOWN'}
- 用量可见性：{report.quota_summary['visibility']}
- QPS：{report.quota_summary['qps_limit'] or 'UNKNOWN'}
- 周额度：{report.quota_summary['weekly_limit'] or 'UNKNOWN'}

## 环境与依赖

- Windows：{report.environment.windows_version}
- 架构：{report.environment.architecture}
- Python：{report.environment.python_version}
- Conda：{report.environment.conda_environment}
- Native `.pyd`：{report.environment.native_pyd_count}
- Native `.dll`：{report.environment.native_dll_count}
- iFinD 客户端依赖：{report.packaging_summary['requires_ifind_client']}
- 许可证确认：{report.packaging_summary['license_confirmed']}

## 审计样本

| 代码 | 市场 | 板块 | 本地最新交易日 | 来源 |
|---|---|---|---|---|
{samples}

## 能力矩阵

| 类别 | 能力 | 状态 | 错误分类 | 接入建议 |
|---|---|---|---|---|
{rows}

## 调用与配额

- 配置上限：{report.call_summary.configured_maximum}
- 硬上限：{report.call_summary.hard_maximum}
- 实际调用：{report.call_summary.actual_calls}
- 成功调用：{report.call_summary.successful_calls}
- 有效空结果：{report.call_summary.empty_valid_calls}
- 未授权结果：{report.call_summary.unauthorized_calls}
- 失败调用：{report.call_summary.failed_calls}
- 调用间隔：{report.call_summary.call_pacing_ms} ms
- QPS/周配额：未执行真实登录，无法确认

## 数据时效与历史深度

- 实时快照：NOT_TESTED，Provider 时间 UNKNOWN，本地观测时间 UNKNOWN，延迟 UNKNOWN，状态 TIME_UNKNOWN。
- 分钟行情：NOT_TESTED，最新 Bar 时间 UNKNOWN，延迟 UNKNOWN，状态 TIME_UNKNOWN。
- 集合竞价、公告、新闻：NOT_TESTED，发布时间和查询时效均 UNKNOWN。
- 历史起点与最新可用日期：NOT_TESTED。

## 批量能力与流量估算

以下均为 `ESTIMATE_NOT_ACTUAL_USAGE`，不是实际用量。

| 场景 | 能力 | 测试代码数 | 测试指标数 | 返回行数 | 延迟 ms | 估算单元格 |
|---|---|---:|---:|---:|---:|---:|
{batches}

## 打包审计

- iFinD 客户端依赖：{report.packaging_summary['requires_ifind_client']}
- Native DLL 依赖：{report.packaging_summary['requires_native_dll']}
- PyInstaller 随包分发：{report.packaging_summary['pyinstaller_distribution_allowed']}
- 目标电脑单独安装组件：{report.packaging_summary['target_machine_component_install']}
- 再分发许可证：{'CONFIRMED' if report.packaging_summary['license_confirmed'] else 'NOT_CONFIRMED'}

## 当前问题

{problems}

## 安全边界

- 未保存原始响应。
- 未写正式行情业务表。
- 未修改 Tushare 主链。
- 未调用 Quant、LLM、订单或 Scheduler。
- 报告不包含凭据或完整认证响应。

## 接入建议

- P0/P1 候选仅来自预设审计优先级，不代表已验证可用。
- 需要正式账号或数据流产品的能力，必须等真实探针确认授权、时效和配额后再评估。
- Tushare 继续作为日频、基础资料和 Quant 主数据源；本阶段不接入任何 iFinD 生产链路。

## 下一步

通过 Electron SecretManager 配置 SDK 所需的用户名和密码后，再运行受控真实探针；同时继续确认客户端、Native Runtime 和再分发许可证要求。
"""


def _markdown_v2(report: IFindAuditReport) -> str:
    capability_rows = "\n".join(
        "| {name} | {endpoint} | {status} | {rows} | {latency} | {data_status} |".format(
            name=item.capability_name,
            endpoint=item.endpoint or "-",
            status=item.status,
            rows=item.row_count,
            latency=item.observed_latency_ms if item.observed_latency_ms is not None else "-",
            data_status=item.data_status or "TIME_UNKNOWN",
        )
        for item in report.capabilities
    )
    problems = "\n".join(f"- {item}" for item in report.problems) or "- 无"
    return f"""# iFinD HTTP 能力审计

审计时间：{report.audited_at.isoformat()}

## 结论

- 模式：{report.mode}
- 状态：{report.overall_status}
- 传输方式：{report.authentication.get('transport', 'UNKNOWN')}
- HTTP 认证：{report.authentication.get('auth_status', report.authentication.get('login_status', 'UNKNOWN'))}
- 认证调用：{report.authentication.get('auth_calls', 0)}
- 数据调用：{report.call_summary.actual_calls}/{report.call_summary.configured_maximum}
- 成功调用：{report.call_summary.successful_calls}
- 失败调用：{report.call_summary.failed_calls}
- 正式业务表写入：{report.business_table_writes}
- 生产路由变更：{report.production_route_changed}
- Scheduler：{report.scheduler_enabled}
- 真实交易：{report.real_trading_enabled}
- 原始响应保存：{report.raw_response_saved}

## 能力矩阵

| 能力 | Endpoint | 状态 | 行数 | 延迟 ms | 数据状态 |
|---|---|---|---:|---:|---|
{capability_rows}

## 认证与安全

- 凭据状态：{report.authentication.get('credential_status', 'UNKNOWN')}
- SDK 凭据状态：{report.authentication.get('sdk_credential_status', 'UNKNOWN')}
- HTTP 凭据状态：{report.authentication.get('http_credential_status', 'UNKNOWN')}
- Access Token 仅保存在进程内存和本地加密/环境配置中。
- 报告不包含 Token、用户名、密码、请求 Header 或完整原始响应。
- Tushare 继续作为全 A 日频、Quant、财务和历史回放主数据源。
- iFinD 生产开关和盘中监控保持关闭。

## 当前问题

{problems}
"""


def _safe_report_path(path: Path) -> str:
    return path.as_posix() if not path.is_absolute() else f"<project>/{path.name}"


def _status_counts(report: IFindAuditReport) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in report.capabilities:
        result[item.status.value] = result.get(item.status.value, 0) + 1
    return result


def _secret_values() -> list[str]:
    return [
        os.getenv("IFIND_ACCESS_TOKEN", ""), os.getenv("IFIND_REFRESH_TOKEN", ""),
        os.getenv("IFIND_API_KEY", ""), os.getenv("IFIND_USERNAME", ""), os.getenv("IFIND_PASSWORD", ""),
    ]


def _assert_sanitized(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    for value in _secret_values():
        if value and value in text:
            raise ValueError("IFIND_SECRET_LEAK_DETECTED")
    forbidden = {"reasoning_content", "authorization_header", "raw_response", "cookie"}
    if any(key.lower() in forbidden for key in _walk_keys(payload)):
        raise ValueError("IFIND_REPORT_FORBIDDEN_FIELD")


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)
