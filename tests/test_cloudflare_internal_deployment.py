from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.cloudflare.bootstrap_internal_access import (
    APP_NAME,
    POLICY_NAME,
    _manual_configuration,
    apply,
    inspect,
)
from scripts.cloudflare.common import CloudflareApiError, DeploymentInput, write_local_state


EMAILS = tuple(f"user{index}@example.com" for index in range(1, 5))


def values(tmp_path: Path, monkeypatch, *, hostname="trader.example.com") -> DeploymentInput:
    monkeypatch.setenv("CLOUDFLARE_TUNNEL_TOKEN_FILE", str(tmp_path / "tunnel-token.txt"))
    monkeypatch.setenv("AI_TRADER_SERVER_DATA_ROOT", str(tmp_path / "programdata"))
    return DeploymentInput(
        account_id="a" * 32,
        zone_id="z" * 32,
        token="api-token-must-never-be-emitted",
        hostname=hostname,
        tunnel_name="ai-trader-internal",
        emails=EMAILS,
        session_hours=12,
    )


class FakeCloudflare:
    def __init__(self, deployment: DeploymentInput) -> None:
        self.values = deployment
        self.calls: list[tuple[str, str]] = []
        self.tunnel = {"id": "tunnel-id", "name": deployment.tunnel_name, "status": "healthy"}
        self.otp = {"id": "otp-id", "name": "One-time PIN", "type": "onetimepin"}
        self.app = {
            "id": "app-id", "aud": "aud-id", "name": APP_NAME, "type": "self_hosted",
            "domain": deployment.hostname, "session_duration": "12h", "auto_redirect_to_identity": True,
            "allowed_idps": ["otp-id"], "allow_authenticate_via_warp": False,
        }
        self.policy = {
            "id": "policy-id", "name": POLICY_NAME, "decision": "allow", "precedence": 1,
            "include": [{"email": {"email": email}} for email in deployment.emails],
            "exclude": [], "require": [],
        }
        self.records = [{
            "id": "dns-id", "type": "CNAME", "name": deployment.hostname,
            "content": "tunnel-id.cfargotunnel.com", "proxied": True,
        }]
        self.config = {"config": {"ingress": [
            {"hostname": deployment.hostname, "service": "http://127.0.0.1:8080", "originRequest": {"connectTimeout": 10}},
            {"service": "http_status:404"},
        ]}}

    def zone(self): return {"id": self.values.zone_id, "name": "example.com"}
    def organization(self): return {"auth_domain": "team.cloudflareaccess.com"}
    def tunnels(self): return [self.tunnel] if self.tunnel else []
    def apps(self): return [self.app] if self.app else []
    def identity_providers(self): return [self.otp] if self.otp else []
    def dns_records(self): return list(self.records)

    def request(self, method, path, *, params=None, json_body=None):
        self.calls.append((method, path))
        if path.endswith("/policies") and method == "GET": return [self.policy] if self.policy else []
        if path.endswith("/configurations") and method == "GET": return self.config
        if path.endswith("/token") and method == "GET": return "tunnel-token-" + "x" * 80
        if path.endswith("/identity_providers") and method == "POST":
            self.otp = {"id": "otp-id", **json_body}; return self.otp
        if path.endswith("/access/apps") and method == "POST":
            self.app = {"id": "app-id", "aud": "aud-id", **json_body}; return self.app
        if "/access/apps/app-id" in path and method == "PUT" and not path.endswith("/policies/policy-id"):
            self.app = {"id": "app-id", "aud": "aud-id", **json_body}; return self.app
        if path.endswith("/policies") and method == "POST":
            self.policy = {"id": "policy-id", **json_body}; return self.policy
        if path.endswith("/policies/policy-id") and method == "PUT":
            self.policy = {"id": "policy-id", **json_body}; return self.policy
        if "/policies/" in path and method == "DELETE": return {}
        if path.endswith("/cfd_tunnel") and method == "POST":
            self.tunnel = {"id": "tunnel-id", "status": "inactive", **json_body}; return self.tunnel
        if path.endswith("/configurations") and method == "PUT":
            self.config = json_body; return self.config
        if path.endswith("/dns_records") and method == "POST":
            self.records = [{"id": "dns-id", **json_body}]; return self.records[0]
        if "/dns_records/" in path and method == "PUT":
            self.records = [{"id": "dns-id", **json_body}]; return self.records[0]
        raise AssertionError(f"Unexpected fake request: {method} {path}")


def test_exact_configuration_verifies_ready(tmp_path, monkeypatch):
    deployment = values(tmp_path, monkeypatch)
    result = inspect(FakeCloudflare(deployment), deployment)
    assert result["ready"] is True
    assert result["policy"] == {
        "exists": True, "id": "policy-id", "exact": True, "unsafe_policy_count": 0, "bypass": False
    }
    assert result["planned_changes"] == []


def test_bypass_or_non_exact_policy_fails_closed(tmp_path, monkeypatch):
    deployment = values(tmp_path, monkeypatch)
    client = FakeCloudflare(deployment)
    client.policy["decision"] = "bypass"
    result = inspect(client, deployment)
    assert result["ready"] is False
    assert result["policy"]["bypass"] is True
    assert "exact_four_user_policy" in result["planned_changes"]


def test_root_hostname_is_rejected(tmp_path, monkeypatch):
    deployment = values(tmp_path, monkeypatch, hostname="example.com")
    with pytest.raises(CloudflareApiError, match="HOSTNAME_MUST_BE_NON_ROOT_ZONE_SUBDOMAIN"):
        inspect(FakeCloudflare(deployment), deployment)


def test_apply_is_idempotent_and_access_precedes_dns(tmp_path, monkeypatch):
    deployment = values(tmp_path, monkeypatch)
    client = FakeCloudflare(deployment)
    client.records = []
    monkeypatch.setattr(
        "scripts.cloudflare.bootstrap_internal_access.subprocess.run",
        lambda *args, **kwargs: type("Completed", (), {"returncode": 0})(),
    )
    first = apply(client, deployment)
    second = apply(client, deployment)
    assert first["ready"] is True and second["ready"] is True
    assert Path(first["tunnel_token_file"]).read_text(encoding="ascii").startswith("tunnel-token-")
    dns_writes = [index for index, call in enumerate(client.calls) if "/dns_records" in call[1] and call[0] in {"POST", "PUT"}]
    policy_writes = [index for index, call in enumerate(client.calls) if "/policies/" in call[1] and call[0] == "PUT"]
    assert dns_writes and policy_writes and max(index for index in policy_writes if index < min(dns_writes)) < min(dns_writes)
    serialized = json.dumps({"first": first, "second": second})
    assert deployment.token not in serialized
    assert "tunnel-token-" not in serialized


def test_missing_token_manual_plan_and_state_redact_secrets(tmp_path, monkeypatch):
    deployment = values(tmp_path, monkeypatch)
    deployment = DeploymentInput(**{**deployment.__dict__, "token": ""})
    plan = _manual_configuration(deployment)
    assert plan["status"] == "MANUAL_CONFIGURATION_REQUIRED"
    assert plan["allowed_user_count"] == 4
    monkeypatch.setenv("AI_TRADER_SERVER_DATA_ROOT", str(tmp_path / "programdata"))
    state_path = write_local_state({"token": "secret", "tunnel_token": "secret2", "status": "ok"})
    state = state_path.read_text(encoding="utf-8")
    assert "secret" not in state and json.loads(state) == {"status": "ok"}


def test_apply_requires_exactly_four_unique_emails(tmp_path, monkeypatch):
    deployment = values(tmp_path, monkeypatch)
    invalid = DeploymentInput(**{**deployment.__dict__, "emails": EMAILS[:3]})
    with pytest.raises(CloudflareApiError, match="EXACTLY_FOUR_ALLOWED_EMAILS_REQUIRED"):
        apply(FakeCloudflare(invalid), invalid)


def test_apply_refuses_preexisting_dns_until_access_is_exact(tmp_path, monkeypatch):
    deployment = values(tmp_path, monkeypatch)
    client = FakeCloudflare(deployment)
    client.app = None
    with pytest.raises(CloudflareApiError, match="PREEXISTING_DNS_WITHOUT_EXACT_ACCESS"):
        apply(client, deployment)
    assert not any(method in {"POST", "PUT", "DELETE"} for method, _ in client.calls)


def test_shared_password_mode_uses_only_tunnel_and_dns(tmp_path, monkeypatch):
    deployment = DeploymentInput(**{
        **values(tmp_path, monkeypatch).__dict__,
        "auth_mode": "LOCAL_SHARED_PASSWORD",
        "emails": (),
    })
    client = FakeCloudflare(deployment)
    monkeypatch.setattr(
        "scripts.cloudflare.bootstrap_internal_access.subprocess.run",
        lambda *args, **kwargs: type("Completed", (), {"returncode": 0})(),
    )
    result = apply(client, deployment)
    assert result["authentication_mode"] == "LOCAL_SHARED_PASSWORD"
    assert result["access_application"] == {"required": False, "exists": False}
    assert result["required_permissions"] == ["Cloudflare Tunnel Edit", "DNS Edit", "Zone Read"]
    assert not any("/access/" in path for _, path in client.calls)


def test_shared_password_mode_refuses_conflicting_existing_dns(tmp_path, monkeypatch):
    deployment = DeploymentInput(**{
        **values(tmp_path, monkeypatch).__dict__,
        "auth_mode": "LOCAL_SHARED_PASSWORD",
        "emails": (),
    })
    client = FakeCloudflare(deployment)
    client.records[0]["content"] = "unmanaged.cfargotunnel.com"
    with pytest.raises(CloudflareApiError, match="PREEXISTING_DNS_CONFLICT"):
        apply(client, deployment)
    assert not any(method in {"POST", "PUT", "DELETE"} for method, _ in client.calls)
