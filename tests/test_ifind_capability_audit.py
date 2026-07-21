from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.api import ifind as ifind_api
from backend.main import create_app
from database.base import Base
from database.models.system import ConfigHistory, LLMUsage
from database.models.validation import ModelValidationOrderPlan
from database.session import create_engine_from_url
from datasource.ifind.capability import observed_delay_seconds
from datasource.ifind.errors import classify_error, sanitize_error
from datasource.ifind.http.errors import IFindHttpError, IFindHttpErrorCategory
from datasource.ifind.http.schemas import IFindHttpAuthResult
from datasource.ifind.function_registry import discover_function_registry
from datasource.ifind.probe import IFindCapabilityProbe, ProbeCallLimitReached, SerialProbeLimiter, load_latest_audit
from datasource.ifind.schemas import CapabilityResult, CapabilityStatus, IFindErrorCategory, ProductionRecommendation
from datasource.ifind.session import IFindSessionError, IFindSessionManager


def _factory(tmp_path: Path):
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'ifind.db').as_posix()}")
    import database.models  # noqa: F401

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _config(**updates):
    values = {
        "max_total_calls": 30, "hard_max_total_calls": 50, "min_interval_ms": 1000,
        "timeout_seconds": 30, "sample_stock_count": 4, "sample_index_count": 2,
        "save_raw_response": False, "persist_business_data": False,
        "sdk_module_candidates": ["iFinDPy"], "login_function": None, "logout_function": None,
        "fallback_samples": {
            "stocks": [
                {"canonical_code": "600000.SH", "market": "SH", "board": "MAIN"},
                {"canonical_code": "000001.SZ", "market": "SZ", "board": "MAIN"},
                {"canonical_code": "300750.SZ", "market": "SZ", "board": "CHINEXT"},
                {"canonical_code": "688981.SH", "market": "SH", "board": "STAR"},
            ],
            "indices": [
                {"canonical_code": "000001.SH", "market": "SH", "board": "INDEX"},
                {"canonical_code": "399001.SZ", "market": "SZ", "board": "INDEX"},
            ],
        },
    }
    values.update(updates)
    return values


def test_session_reports_sdk_not_installed_without_importing() -> None:
    loaded = []
    manager = IFindSessionManager(_config(), module_finder=lambda _name: None, module_loader=lambda name: loaded.append(name))
    audit = manager.inspect_environment()
    assert audit.sdk_status == "SDK_NOT_INSTALLED"
    assert audit.sdk_install_location is None
    assert loaded == []
    assert manager.call_count == 0


def test_function_registry_uses_actual_exported_names() -> None:
    module = ModuleType("iFinDPy")
    module.THS_iFinDLogin = lambda username, password: 0
    module.THS_iFinDLogout = lambda: 0
    module.THS_HQ = lambda codes, indicators, params, start, end: None
    rows = {row.canonical_capability: row for row in discover_function_registry(module)}
    assert rows["authentication.login"].actual_function_name == "THS_iFinDLogin"
    assert rows["history_quotes"].actual_function_name == "THS_HQ"
    assert rows["realtime_quotes"].available is False
    assert rows["authentication.login"].signature_summary == "(username, password)"


def test_session_classifies_native_dependency_failure() -> None:
    manager = IFindSessionManager(
        _config(), module_finder=lambda _name: object(),
        module_loader=lambda _name: (_ for _ in ()).throw(OSError("DLL load failed")),
    )
    audit = manager.inspect_environment()
    assert audit.sdk_status == "NATIVE_DEPENDENCY_MISSING"
    assert "DLL load failed" in (audit.sanitized_error or "")


def test_session_mock_login_and_safe_logout_on_exception(monkeypatch) -> None:
    calls = []
    module = ModuleType("mock_ifind")
    module.__file__ = __file__
    module.__version__ = "test"
    module.login = lambda access_token: calls.append(("login", access_token)) or 0
    module.logout = lambda: calls.append(("logout", None)) or 0
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-secret-value")
    manager = IFindSessionManager(
        _config(
            sdk_module_candidates=["mock_ifind"], login_function="login", logout_function="logout",
            login_arguments={"access_token": "IFIND_ACCESS_TOKEN"}, min_interval_ms=1000,
        ),
        module_finder=lambda _name: object(), module_loader=lambda _name: module, sleep=lambda _seconds: None,
    )
    with pytest.raises(RuntimeError, match="business failure"):
        with manager:
            assert manager.login() == "LOGIN_SUCCESS"
            raise RuntimeError("business failure")
    assert [item[0] for item in calls] == ["login", "logout"]
    assert manager.logged_in is False
    assert manager.call_count == 2


def test_session_login_failure_is_not_permission_error(monkeypatch) -> None:
    module = ModuleType("mock_ifind")
    module.__file__ = __file__
    module.login = lambda access_token: (_ for _ in ()).throw(RuntimeError("login failed"))
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-secret-value")
    manager = IFindSessionManager(
        _config(sdk_module_candidates=["mock_ifind"], login_function="login", login_arguments={"access_token": "IFIND_ACCESS_TOKEN"}),
        module_finder=lambda _name: object(), module_loader=lambda _name: module,
    )
    manager.inspect_environment()
    with pytest.raises(IFindSessionError) as captured:
        manager.login()
    assert captured.value.category == IFindErrorCategory.LOGIN_FAILED
    assert manager.call_count == 1
    assert manager.success_count == 0
    assert manager.failed_count == 1


@pytest.mark.parametrize(("message", "expected"), [
    ("DLL load failed", IFindErrorCategory.NATIVE_DEPENDENCY_MISSING),
    ("client not running", IFindErrorCategory.CLIENT_NOT_RUNNING),
    ("credential not configured", IFindErrorCategory.NOT_CONFIGURED),
    ("login failed", IFindErrorCategory.LOGIN_FAILED),
    ("account expired", IFindErrorCategory.ACCOUNT_EXPIRED),
    ("not authorized", IFindErrorCategory.NOT_AUTHORIZED),
    ("trial limit", IFindErrorCategory.TRIAL_LIMIT),
    ("invalid parameter", IFindErrorCategory.INVALID_PARAMETER),
    ("empty valid", IFindErrorCategory.EMPTY_VALID_RESULT),
    ("rate limit", IFindErrorCategory.RATE_LIMITED),
    ("quota exhausted", IFindErrorCategory.QUOTA_EXHAUSTED),
    ("timed out", IFindErrorCategory.TIMEOUT),
    ("network error", IFindErrorCategory.NETWORK_ERROR),
    ("provider error", IFindErrorCategory.PROVIDER_ERROR),
    ("response schema invalid", IFindErrorCategory.RESPONSE_SCHEMA_ERROR),
    ("unsupported by sdk", IFindErrorCategory.UNSUPPORTED_BY_SDK),
    ("unrecognized", IFindErrorCategory.UNKNOWN_ERROR),
])
def test_error_classifier_covers_required_categories(message, expected) -> None:
    assert classify_error(message) == expected


def test_error_sanitizer_removes_credentials() -> None:
    secret = "long-test-access-token-value-1234567890"
    sanitized = sanitize_error(f"login failed access_token={secret}", [secret])
    assert secret not in sanitized
    assert "[REDACTED]" in sanitized


def test_serial_limiter_enforces_pacing_and_max_calls() -> None:
    sleeps = []
    limiter = SerialProbeLimiter(2, 2, 1000, sleep=sleeps.append)
    assert limiter.call(lambda: "first")[0] == "first"
    assert limiter.call(lambda: "second")[0] == "second"
    with pytest.raises(ProbeCallLimitReached):
        limiter.call(lambda: "third")
    assert limiter.call_count == 2
    assert sleeps and sleeps[0] > 0


def test_dry_run_has_zero_calls_no_business_writes_and_sanitized_reports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "local-test-secret-not-for-report")
    factory = _factory(tmp_path)
    session = factory()
    before = {
        "llm": session.scalar(select(func.count()).select_from(LLMUsage)),
        "orders": session.scalar(select(func.count()).select_from(ModelValidationOrderPlan)),
        "history": session.scalar(select(func.count()).select_from(ConfigHistory)),
    }
    manager = IFindSessionManager(_config(), module_finder=lambda _name: None)
    report = IFindCapabilityProbe(session, _config(), output_dir=tmp_path / "reports", session_manager=manager, cache_root=tmp_path / "cache").run()
    after = {
        "llm": session.scalar(select(func.count()).select_from(LLMUsage)),
        "orders": session.scalar(select(func.count()).select_from(ModelValidationOrderPlan)),
        "history": session.scalar(select(func.count()).select_from(ConfigHistory)),
    }
    assert report.mode == "DRY_RUN"
    assert report.environment.sdk_status == "SDK_NOT_INSTALLED"
    assert report.call_summary.actual_calls == 0
    assert report.business_table_writes == 0
    assert report.quant_or_llm_called is False
    assert report.raw_response_saved is False
    assert before == after
    contents = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "reports").glob("*.json"))
    assert "local-test-secret-not-for-report" not in contents
    assert "raw_response\"" not in contents
    assert (tmp_path / "docs" / "IFIND_TRIAL_CAPABILITY_AUDIT.md").exists()
    session.close()


def test_real_probe_gate_stops_before_call_when_sdk_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-secret")
    monkeypatch.setenv("IFIND_USERNAME", "test-user")
    monkeypatch.setenv("IFIND_PASSWORD", "test-password")
    monkeypatch.setenv("RUN_REAL_IFIND_PROBE", "true")
    monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "schedule.yaml").write_text("schedule:\n  intraday_monitor:\n    enabled: false\n", encoding="utf-8")
    factory = _factory(tmp_path)
    session = factory()
    manager = IFindSessionManager(_config(), module_finder=lambda _name: None)
    report = IFindCapabilityProbe(session, _config(), output_dir=tmp_path / "reports", session_manager=manager, cache_root=tmp_path / "cache").run(real_probe=True, transport="sdk")
    assert report.overall_status == "SDK_NOT_INSTALLED"
    assert report.call_summary.actual_calls == 0
    assert report.authentication["login_status"] == "UNKNOWN"
    session.close()


def test_access_token_does_not_satisfy_username_password_sdk_login(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-access-token")
    monkeypatch.delenv("IFIND_USERNAME", raising=False)
    monkeypatch.delenv("IFIND_PASSWORD", raising=False)
    monkeypatch.setenv("RUN_REAL_IFIND_PROBE", "true")
    monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "schedule.yaml").write_text("schedule:\n  intraday_monitor:\n    enabled: false\n", encoding="utf-8")
    session = _factory(tmp_path)()
    config = _config(login_function="THS_iFinDLogin", login_arguments={"username": "IFIND_USERNAME", "password": "IFIND_PASSWORD"})
    manager = IFindSessionManager(config, module_finder=lambda _name: None)
    report = IFindCapabilityProbe(session, config, output_dir=tmp_path / "reports", session_manager=manager, cache_root=tmp_path / "cache").run(real_probe=True, transport="sdk")
    assert report.overall_status == "IFIND_CREDENTIAL_NOT_CONFIGURED"
    assert report.authentication["credential_status"] == "NOT_CONFIGURED"
    assert report.authentication["authentication_mode"] == "USERNAME_PASSWORD"
    assert report.call_summary.actual_calls == 0
    session.close()


def test_http_auth_failure_stops_before_all_data_calls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIND_REFRESH_TOKEN", "test-refresh-token")
    monkeypatch.setenv("IFIND_HTTP_ENABLED", "true")
    monkeypatch.setenv("RUN_REAL_IFIND_PROBE", "true")
    monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "schedule.yaml").write_text(
        "schedule:\n  intraday_monitor:\n    enabled: false\n", encoding="utf-8",
    )

    class FailingAuth:
        has_refresh_token = True
        has_access_token = False
        auth_calls = 0
        last_result = IFindHttpAuthResult(
            status="INVALID", source="REFRESH_TOKEN", expires_at=None, latency_ms=1,
            error_category="REFRESH_TOKEN_INVALID", response_schema_hash="schema-test",
        )

        def __init__(self, **_kwargs) -> None:
            pass

        def refresh_access_token(self):
            self.auth_calls = 1
            raise IFindHttpError(IFindHttpErrorCategory.REFRESH_TOKEN_INVALID, "Authentication failed")

    monkeypatch.setattr("datasource.ifind.probe.IFindHttpAuthManager", FailingAuth)
    session = _factory(tmp_path)()
    manager = IFindSessionManager(_config(), module_finder=lambda _name: None)
    report = IFindCapabilityProbe(
        session, _config(http_base_url="https://official.example/api/v1"),
        output_dir=tmp_path / "reports", session_manager=manager, cache_root=tmp_path / "cache",
    ).run(real_probe=True, transport="http", stop_on_login_failure=True)
    assert report.overall_status == "HTTP_AUTH_FAILED"
    assert report.authentication["auth_calls"] == 1
    assert report.call_summary.actual_calls == 0
    assert report.call_summary.successful_calls == 0
    session.close()


def test_capability_schema_distinguishes_empty_and_unauthorized() -> None:
    base = dict(category="TEST", capability_name="sample", configured=True, request_scope="one code", production_recommendation=ProductionRecommendation.DO_NOT_USE)
    empty = CapabilityResult(**base, status=CapabilityStatus.EMPTY_VALID, error_category=IFindErrorCategory.EMPTY_VALID_RESULT)
    unauthorized = CapabilityResult(**base, status=CapabilityStatus.NOT_AUTHORIZED, error_category=IFindErrorCategory.NOT_AUTHORIZED)
    assert empty.status != unauthorized.status
    assert set(CapabilityResult.model_fields) >= {"observed_latency_ms", "earliest_available_date", "batch_supported", "response_schema_hash"}


def test_observed_delay_classification() -> None:
    observed = datetime(2026, 7, 14, 10, tzinfo=timezone.utc)
    assert observed_delay_seconds(observed - timedelta(seconds=5), observed) == (5.0, "REALTIME")
    assert observed_delay_seconds(observed - timedelta(seconds=60), observed) == (60.0, "DELAYED")
    assert observed_delay_seconds(observed - timedelta(seconds=600), observed) == (600.0, "STALE")
    assert observed_delay_seconds(None, observed) == (None, "TIME_UNKNOWN")


def test_latest_capability_api_returns_sanitized_report(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "ifind"
    output.mkdir()
    payload = {"overall_status": "DRY_RUN", "environment": {"sdk_status": "SDK_NOT_INSTALLED"}, "authentication": {"credential_status": "CONFIGURED", "login_status": "UNKNOWN"}, "capabilities": [], "call_summary": {"actual_calls": 0}}
    (output / "ifind_capability_audit_20260714_120000.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(ifind_api, "report_root", lambda: tmp_path)
    response = TestClient(create_app()).get("/api/data-sources/ifind/capabilities/latest")
    assert response.status_code == 200
    assert response.json()["data"]["environment"]["sdk_status"] == "SDK_NOT_INSTALLED"
    assert "token" not in response.text.lower()


def test_ifind_configuration_remains_disabled_and_tushare_primary() -> None:
    payload = yaml.safe_load(Path("config/data_sources.yaml").read_text(encoding="utf-8"))["data_sources"]
    assert payload["market_primary"]["provider"] == "tushare"
    assert payload["market_primary"]["enabled"] is True
    assert payload["ifind"]["enabled"] is False
    assert all(payload["ifind"][key] is False for key in payload["ifind"] if key.startswith("use_for_"))
    schedule = yaml.safe_load(Path("config/schedule.yaml").read_text(encoding="utf-8"))["schedule"]
    assert not any(item["enabled"] for item in schedule.values())


def test_probe_rejects_unsafe_limits_and_persistence(tmp_path: Path) -> None:
    session = _factory(tmp_path)()
    with pytest.raises(ValueError, match="CALL_LIMIT"):
        IFindCapabilityProbe(session, _config(hard_max_total_calls=51), output_dir=tmp_path)
    with pytest.raises(ValueError, match="PERSISTENCE"):
        IFindCapabilityProbe(session, _config(save_raw_response=True), output_dir=tmp_path)
    session.close()


def test_load_latest_audit_returns_none_without_report(tmp_path: Path) -> None:
    assert load_latest_audit(tmp_path) is None


def test_skip_packaging_audit_is_reflected_in_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session = _factory(tmp_path)()
    manager = IFindSessionManager(_config(), module_finder=lambda _name: None)
    report = IFindCapabilityProbe(
        session,
        _config(skip_packaging_audit=True),
        output_dir=tmp_path / "reports",
        session_manager=manager,
        cache_root=tmp_path / "cache",
    ).run()
    assert report.environment.packaging_feasibility == "NOT_AUDITED"
    assert report.packaging_summary["feasibility"] == "NOT_AUDITED"
    session.close()
