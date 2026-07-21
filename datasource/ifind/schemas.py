from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IFindErrorCategory(StrEnum):
    SDK_NOT_INSTALLED = "SDK_NOT_INSTALLED"
    NATIVE_DEPENDENCY_MISSING = "NATIVE_DEPENDENCY_MISSING"
    CLIENT_NOT_RUNNING = "CLIENT_NOT_RUNNING"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    LOGIN_FAILED = "LOGIN_FAILED"
    ACCOUNT_EXPIRED = "ACCOUNT_EXPIRED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    TRIAL_LIMIT = "TRIAL_LIMIT"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    EMPTY_VALID_RESULT = "EMPTY_VALID_RESULT"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RESPONSE_SCHEMA_ERROR = "RESPONSE_SCHEMA_ERROR"
    UNSUPPORTED_BY_SDK = "UNSUPPORTED_BY_SDK"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    REFRESH_TOKEN_NOT_CONFIGURED = "REFRESH_TOKEN_NOT_CONFIGURED"
    REFRESH_TOKEN_INVALID = "REFRESH_TOKEN_INVALID"
    ACCESS_TOKEN_INVALID = "ACCESS_TOKEN_INVALID"
    ACCESS_TOKEN_EXPIRED = "ACCESS_TOKEN_EXPIRED"
    DEVICE_LIMIT_EXCEEDED = "DEVICE_LIMIT_EXCEEDED"


class CapabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    AVAILABLE_DELAYED = "AVAILABLE_DELAYED"
    AVAILABLE_PARTIAL = "AVAILABLE_PARTIAL"
    EMPTY_VALID = "EMPTY_VALID"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    TRIAL_LIMITED = "TRIAL_LIMITED"
    UNSUPPORTED = "UNSUPPORTED"
    ERROR = "ERROR"
    NOT_TESTED = "NOT_TESTED"


class ProductionRecommendation(StrEnum):
    P0_INTEGRATE = "P0_INTEGRATE"
    P1_INTEGRATE = "P1_INTEGRATE"
    P2_OPTIONAL = "P2_OPTIONAL"
    KEEP_TUSHARE_PRIMARY = "KEEP_TUSHARE_PRIMARY"
    NOT_SUITABLE_INTRADAY = "NOT_SUITABLE_INTRADAY"
    NEED_FORMAL_ACCOUNT = "NEED_FORMAL_ACCOUNT"
    NEED_DATAFEED_PRODUCT = "NEED_DATAFEED_PRODUCT"
    DO_NOT_USE = "DO_NOT_USE"


class SampleSelection(StrictModel):
    canonical_code: str
    market: str
    board: str
    selection_reason: str
    local_latest_trade_date: str | None = None
    source: str


class IFindSdkFunctionRegistry(StrictModel):
    canonical_capability: str
    actual_function_name: str | None = None
    callable: bool
    signature_summary: str | None = None
    source_module: str
    available: bool


class SDKEnvironmentAudit(StrictModel):
    windows_version: str
    architecture: str
    python_version: str
    conda_environment: str
    sdk_status: str
    sdk_module: str | None = None
    distribution_name: str | None = None
    sdk_version: str | None = None
    sdk_install_location: str | None = None
    package_record_sha256: str | None = None
    native_pyd_count: int = 0
    native_dll_count: int = 0
    native_so_count: int = 0
    pth_count: int = 0
    public_functions: list[dict[str, str]] = Field(default_factory=list)
    function_registry: list[IFindSdkFunctionRegistry] = Field(default_factory=list)
    subprocess_import_status: str
    client_dependency: str
    local_service_dependency: str
    pure_http_mode: str
    packaging_feasibility: str
    pyinstaller_hidden_import_present: bool
    license_redistribution_status: str = "NOT_CONFIRMED"
    sanitized_error: str | None = None


class CapabilityResult(StrictModel):
    category: str
    capability_name: str
    sdk_function: str | None = None
    sdk_signature_summary: str | None = None
    status: CapabilityStatus
    authorized: bool | None = None
    configured: bool
    sample_codes: list[str] = Field(default_factory=list)
    request_scope: str
    call_count: int = 0
    row_count: int = 0
    column_count: int = 0
    returned_fields: list[str] = Field(default_factory=list)
    latest_data_time: str | None = None
    provider_server_time: str | None = None
    observed_latency_ms: int | None = None
    observed_market_delay_seconds: float | None = None
    earliest_available_date: str | None = None
    latest_available_date: str | None = None
    batch_supported: bool | None = None
    tested_batch_size: int = 0
    trial_restriction: str | None = None
    quota_units_consumed: float | None = None
    qps_limit: float | None = None
    weekly_limit: float | None = None
    response_schema_hash: str | None = None
    error_category: IFindErrorCategory | None = None
    sanitized_error: str | None = None
    production_recommendation: ProductionRecommendation
    notes: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    local_observation_time: str | None = None
    market_session: str | None = None
    data_status: str | None = None


class ProbeCallSummary(StrictModel):
    configured_maximum: int
    hard_maximum: int
    actual_calls: int
    successful_calls: int
    empty_valid_calls: int
    unauthorized_calls: int
    failed_calls: int
    call_pacing_ms: int
    sequential: bool
    limit_status: str


class IFindAuditReport(StrictModel):
    schema_version: str = "ifind_capability_audit_v1"
    audit_id: str
    audited_at: datetime
    mode: str
    overall_status: str
    environment: SDKEnvironmentAudit
    authentication: dict[str, Any]
    trial_account: dict[str, Any]
    quota_summary: dict[str, Any]
    samples: list[SampleSelection]
    capabilities: list[CapabilityResult]
    call_summary: ProbeCallSummary
    batch_estimates: list[dict[str, Any]]
    packaging_summary: dict[str, Any]
    business_table_writes: int
    production_route_changed: bool
    scheduler_enabled: bool
    real_trading_enabled: bool
    quant_or_llm_called: bool
    raw_response_saved: bool
    report_path: str | None = None
    call_summary_path: str | None = None
    problems: list[str] = Field(default_factory=list)
