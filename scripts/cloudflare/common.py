from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


API_ROOT = "https://api.cloudflare.com/client/v4"


class CloudflareApiError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class DeploymentInput:
    account_id: str
    zone_id: str
    token: str
    hostname: str
    tunnel_name: str
    emails: tuple[str, ...]
    session_hours: int
    auth_mode: str = "CLOUDFLARE_ACCESS"

    @classmethod
    def from_env(cls) -> "DeploymentInput":
        return cls(
            account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip(),
            zone_id=os.getenv("CLOUDFLARE_ZONE_ID", "").strip(),
            token=os.getenv("CLOUDFLARE_API_TOKEN", "").strip(),
            hostname=os.getenv("APP_PUBLIC_HOSTNAME", "").strip().lower(),
            tunnel_name=os.getenv("CLOUDFLARE_TUNNEL_NAME", "ai-trader-internal").strip(),
            emails=tuple(sorted({item.strip().lower() for item in os.getenv("ALLOWED_USER_EMAILS", "").split(",") if item.strip()})),
            session_hours=int(os.getenv("CLOUDFLARE_ACCESS_SESSION_HOURS", "12")),
            auth_mode=os.getenv("AUTH_MODE", "CLOUDFLARE_ACCESS").strip().upper(),
        )

    @property
    def uses_access(self) -> bool:
        return self.auth_mode in {"CLOUDFLARE_ACCESS", "CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD"}

    def validate(self, *, require_token: bool, require_emails: bool = True) -> None:
        missing = [name for name, value in (
            ("CLOUDFLARE_ACCOUNT_ID", self.account_id),
            ("CLOUDFLARE_ZONE_ID", self.zone_id),
            ("APP_PUBLIC_HOSTNAME", self.hostname),
        ) if not value]
        if require_token and not self.token:
            missing.append("CLOUDFLARE_API_TOKEN")
        if missing:
            raise CloudflareApiError("MISSING_DEPLOYMENT_INPUT:" + ",".join(missing))
        if require_emails and self.uses_access and len(self.emails) != 4:
            raise CloudflareApiError("EXACTLY_FOUR_ALLOWED_EMAILS_REQUIRED")
        if self.auth_mode not in {"CLOUDFLARE_ACCESS", "CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD", "LOCAL_SHARED_PASSWORD"}:
            raise CloudflareApiError("INVALID_AUTH_MODE")
        if self.hostname and ("/" in self.hostname or ":" in self.hostname):
            raise CloudflareApiError("APP_PUBLIC_HOSTNAME_INVALID")
        if not 1 <= self.session_hours <= 24:
            raise CloudflareApiError("ACCESS_SESSION_HOURS_INVALID")


class CloudflareClient:
    def __init__(self, values: DeploymentInput) -> None:
        self.values = values
        self.client = httpx.Client(
            base_url=API_ROOT,
            headers={"Authorization": f"Bearer {values.token}", "Content-Type": "application/json"},
            timeout=20,
            follow_redirects=False,
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, *, params=None, json_body=None) -> Any:
        response = self.client.request(method, path, params=params, json=json_body)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudflareApiError("CLOUDFLARE_NON_JSON_RESPONSE", status_code=response.status_code) from exc
        if response.status_code >= 400 or not payload.get("success", False):
            errors = payload.get("errors") or []
            code = str(errors[0].get("code") if errors else response.status_code)
            raise CloudflareApiError(f"CLOUDFLARE_API_DENIED:{code}", status_code=response.status_code)
        return payload.get("result")

    def zone(self) -> dict:
        return self.request("GET", f"/zones/{self.values.zone_id}")

    def tunnels(self) -> list[dict]:
        return list(self.request("GET", f"/accounts/{self.values.account_id}/cfd_tunnel", params={"is_deleted": "false", "per_page": 100}) or [])

    def apps(self) -> list[dict]:
        return list(self.request("GET", f"/accounts/{self.values.account_id}/access/apps", params={"per_page": 100}) or [])

    def organization(self) -> dict:
        return self.request("GET", f"/accounts/{self.values.account_id}/access/organizations")

    def identity_providers(self) -> list[dict]:
        return list(self.request("GET", f"/accounts/{self.values.account_id}/access/identity_providers", params={"per_page": 100}) or [])

    def dns_records(self) -> list[dict]:
        return list(self.request("GET", f"/zones/{self.values.zone_id}/dns_records", params={"name": self.values.hostname, "per_page": 100}) or [])


def safe_result(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key not in {"token", "tunnel_token", "secret", "credentials"}}


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def write_local_state(payload: dict[str, Any]) -> Path:
    root = Path(os.getenv("AI_TRADER_SERVER_DATA_ROOT") or Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "AITraderAssistant")
    path = root / "diagnostics" / "cloudflare-deployment-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe_result(**payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
