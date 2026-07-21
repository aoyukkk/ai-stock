from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass


ROLES = {"ADMIN", "TRADER", "VIEWER"}


@dataclass(frozen=True)
class InternalWebSettings:
    runtime_mode: str
    app_env: str
    public_hostname: str
    origin_host: str
    origin_port: int
    team_domain: str
    access_aud: str
    auth_mode: str
    allowed_users: dict[str, str]
    session_hours: int
    local_bypass: bool
    max_request_bytes: int
    shared_login_username: str = "partners"
    shared_identity_email: str = "shared-partners@local.invalid"

    @property
    def enabled(self) -> bool:
        return self.runtime_mode == "INTERNAL_WEB_SERVER"

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    @property
    def local_password_enabled(self) -> bool:
        return self.auth_mode in {"CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD", "LOCAL_SHARED_PASSWORD"}

    @property
    def cloudflare_access_enabled(self) -> bool:
        return self.auth_mode in {"CLOUDFLARE_ACCESS", "CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD"}

    @property
    def shared_password_enabled(self) -> bool:
        return self.auth_mode == "LOCAL_SHARED_PASSWORD"

    def validate_production(self) -> None:
        if not self.enabled:
            return
        errors = []
        if self.origin_host != "127.0.0.1":
            errors.append("ORIGIN_MUST_BIND_LOOPBACK")
        if not self.public_hostname or "." not in self.public_hostname:
            errors.append("APP_PUBLIC_HOSTNAME_REQUIRED")
        if self.cloudflare_access_enabled:
            if not self.team_domain.endswith(".cloudflareaccess.com"):
                errors.append("CLOUDFLARE_TEAM_DOMAIN_REQUIRED")
            if not self.access_aud:
                errors.append("CLOUDFLARE_ACCESS_AUD_REQUIRED")
            if len(self.allowed_users) != 4:
                errors.append("EXACTLY_FOUR_INTERNAL_USERS_REQUIRED")
        if self.shared_password_enabled and not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", self.shared_login_username):
            errors.append("SHARED_LOGIN_USERNAME_INVALID")
        if "ADMIN" not in self.allowed_users.values():
            errors.append("AT_LEAST_ONE_ADMIN_REQUIRED")
        if self.local_bypass and self.app_env != "development":
            errors.append("LOCAL_AUTH_BYPASS_FORBIDDEN_IN_SERVER_MODE")
        if errors:
            raise RuntimeError(";".join(errors))


def load_internal_web_settings() -> InternalWebSettings:
    runtime_mode = os.getenv("APP_RUNTIME_MODE", "DESKTOP").strip().upper()
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    roles = _role_mapping(os.getenv("INTERNAL_USER_ROLES", ""), os.getenv("ALLOWED_USER_EMAILS", ""))
    auth_mode = os.getenv("AUTH_MODE", "CLOUDFLARE_ACCESS").strip().upper()
    if _flag("ENABLE_LOCAL_PASSWORD_AUTH"):
        auth_mode = "CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD"
    shared_identity_email = "shared-partners@local.invalid"
    if auth_mode == "LOCAL_SHARED_PASSWORD":
        roles = {shared_identity_email: "ADMIN"}
    settings = InternalWebSettings(
        runtime_mode=runtime_mode,
        app_env=app_env,
        public_hostname=os.getenv("APP_PUBLIC_HOSTNAME", "").strip().lower(),
        origin_host=os.getenv("APP_ORIGIN_HOST", "127.0.0.1").strip(),
        origin_port=int(os.getenv("APP_ORIGIN_PORT", "8080")),
        team_domain=_team_domain(os.getenv("CLOUDFLARE_TEAM_DOMAIN", "")),
        access_aud=os.getenv("CLOUDFLARE_ACCESS_AUD", "").strip(),
        auth_mode=auth_mode,
        allowed_users=roles,
        session_hours=max(1, min(24, int(os.getenv("CLOUDFLARE_ACCESS_SESSION_HOURS", "12")))),
        local_bypass=_flag("ALLOW_LOCAL_AUTH_BYPASS"),
        max_request_bytes=max(1024, int(os.getenv("MAX_REQUEST_BODY_BYTES", str(10 * 1024 * 1024)))),
        shared_login_username=os.getenv("SHARED_LOGIN_USERNAME", "partners").strip(),
        shared_identity_email=shared_identity_email,
    )
    if settings.local_bypass and app_env != "development":
        raise RuntimeError("LOCAL_AUTH_BYPASS_REQUIRES_DEVELOPMENT")
    if settings.auth_mode not in {
        "CLOUDFLARE_ACCESS", "CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD", "LOCAL_SHARED_PASSWORD"
    }:
        raise RuntimeError("INVALID_AUTH_MODE")
    return settings


def _role_mapping(raw_roles: str, raw_emails: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if raw_roles.strip():
        try:
            parsed = json.loads(raw_roles)
        except json.JSONDecodeError:
            parsed = dict(item.split(":", 1) for item in raw_roles.split(",") if ":" in item)
        if not isinstance(parsed, dict):
            raise RuntimeError("INVALID_INTERNAL_USER_ROLES")
        for email, role in parsed.items():
            normalized_email = str(email).strip().lower()
            normalized_role = str(role).strip().upper()
            if normalized_email and normalized_role in ROLES:
                result[normalized_email] = normalized_role
    for email in raw_emails.split(","):
        normalized = email.strip().lower()
        if normalized:
            result.setdefault(normalized, "VIEWER")
    return result


def _team_domain(value: str) -> str:
    return value.strip().lower().removeprefix("https://").rstrip("/")


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}
