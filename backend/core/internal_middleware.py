from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.core.internal_auth import (
    AccessTokenError,
    AuthenticatedUser,
    CloudflareAccessVerifier,
    LocalPasswordAuthService,
    load_active_user,
    load_shared_user,
)
from backend.core.internal_settings import InternalWebSettings
from database.models.internal_auth import InternalAuditEvent, InternalPasswordCredential, InternalUser
from database.session import get_auth_session


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ADMIN_PREFIXES = (
    "/api/internal/users",
    "/api/internal/audit",
    "/api/runtime/secrets",
    "/api/workbench/secrets",
    "/api/v1/config",
)
ADMIN_EXACT = {"/api/workbench/runtime/shutdown", "/docs", "/openapi.json", "/redoc"}
ADMIN_WRITE_EXACT = {"/api/workbench/settings"}


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        try:
            if length and int(length) > self.max_bytes:
                await _asgi_error(send, 413, "REQUEST_BODY_TOO_LARGE")
                return
        except ValueError:
            await _asgi_error(send, 400, "INVALID_CONTENT_LENGTH")
            return

        messages: list[Message] = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    await _asgi_error(send, 413, "REQUEST_BODY_TOO_LARGE")
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        index = 0

        async def replay_receive() -> Message:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

class CloudflareAccessAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: InternalWebSettings, verifier=None) -> None:
        super().__init__(app)
        self.settings = settings
        self.verifier = verifier or CloudflareAccessVerifier(settings)

    async def dispatch(self, request: Request, call_next):
        if not _valid_host(request, self.settings):
            return self._error(400, "HOST_NOT_ALLOWED")
        if self.settings.shared_password_enabled and not _server_endpoint(request.url.path):
            response = await call_next(request)
            _security_headers(response, self.settings)
            return response
        try:
            user, cloudflare_verified = await self._authenticate(request)
        except AccessTokenError as exc:
            return self._error(401, str(exc))
        if user is None:
            return self._error(403, "INTERNAL_USER_NOT_ALLOWED")
        request.state.internal_user = user
        request.state.access_email = user.email
        request.state.cloudflare_verified = cloudflare_verified
        request.state.password_change_required = False

        local_session = None
        local_session_required = _server_endpoint(request.url.path)
        if (
            self.settings.local_password_enabled
            and local_session_required
            and request.url.path not in {"/api/internal/auth/login", "/api/internal/auth/local/login", "/api/internal/auth/config/public"}
        ):
            session = get_auth_session()
            try:
                service = LocalPasswordAuthService(session, self.settings.session_hours)
                csrf = request.headers.get("X-CSRF-Token") if request.method not in SAFE_METHODS else None
                if request.method not in SAFE_METHODS and not csrf:
                    return self._error(403, "CSRF_TOKEN_REQUIRED")
                local_session = service.validate(request.cookies.get(service.cookie_name, ""), csrf)
                if local_session is not None and local_session.internal_user_id != user.id:
                    local_session = None
                credential = session.scalar(select(InternalPasswordCredential).where(
                    InternalPasswordCredential.internal_user_id == user.id
                ))
            finally:
                session.close()
            if local_session is None:
                return self._error(401, "LOCAL_SESSION_REQUIRED")
            if credential is None:
                return self._error(401, "LOCAL_PASSWORD_NOT_CONFIGURED")
            request.state.password_change_required = bool(
                self.settings.force_password_change_on_first_login and credential.must_change_password
            )
            if request.state.password_change_required and request.url.path not in {
                "/api/internal/auth/me", "/api/internal/auth/change-password",
                "/api/internal/auth/set-password", "/api/internal/auth/logout"
            }:
                return self._error(403, "PASSWORD_CHANGE_REQUIRED")

        required = _required_role(request.method, request.url.path)
        if required == "ADMIN" and user.role != "ADMIN":
            return self._error(403, "ADMIN_REQUIRED")
        if required == "TRADER" and user.role not in {"ADMIN", "TRADER"}:
            return self._error(403, "WRITE_ACCESS_DENIED")

        response = await call_next(request)
        _security_headers(response, self.settings)
        if request.method not in SAFE_METHODS and response.status_code < 500:
            self._audit(request, user)
        return response

    def _error(self, status: int, code: str) -> JSONResponse:
        response = _error(status, code)
        _security_headers(response, self.settings)
        return response

    async def _authenticate(self, request: Request) -> tuple[AuthenticatedUser | None, bool]:
        if self.settings.local_bypass and _loopback(request):
            session = get_auth_session()
            try:
                row = session.scalar(select(InternalUser).where(InternalUser.role == "ADMIN", InternalUser.active.is_(True)))
                if row is None:
                    row = InternalUser(email="local-admin@localhost", display_name="Local Admin", role="ADMIN", active=True)
                    session.add(row)
                    session.commit()
                return AuthenticatedUser(row.id, row.email, row.display_name, row.role), False
            finally:
                session.close()
        if self.settings.shared_password_enabled:
            session = get_auth_session()
            try:
                return load_shared_user(session, self.settings, mark_login=False), False
            finally:
                session.close()
        claims = await self.verifier.verify(request.headers.get("Cf-Access-Jwt-Assertion", ""))
        session = get_auth_session()
        try:
            return load_active_user(session, str(claims["email"])), True
        finally:
            session.close()

    def _audit(self, request: Request, user: AuthenticatedUser) -> None:
        source_ip = request.headers.get("CF-Connecting-IP") if request.state.cloudflare_verified else (request.client.host if request.client else None)
        agent = request.headers.get("User-Agent", "")[:1024]
        session = get_auth_session()
        try:
            session.add(InternalAuditEvent(
                access_email=user.email,
                internal_user_id=user.id,
                role=user.role,
                source_ip=source_ip,
                user_agent_summary=hashlib.sha256(agent.encode("utf-8")).hexdigest()[:24] if agent else None,
                operation=f"{request.method}:{request.url.path}",
                entity_type="http_request",
                entity_id=request.path_params.get("job_id") or request.path_params.get("selection_id"),
                job_id=request.path_params.get("job_id"),
                metadata_json={"status": "accepted"},
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()
        finally:
            session.close()


def _required_role(method: str, path: str) -> str:
    if path in {
        "/api/internal/auth/login",
        "/api/internal/auth/local/login",
        "/api/internal/auth/logout",
        "/api/internal/auth/change-password",
    }:
        return "VIEWER"
    if path in ADMIN_EXACT or (method not in SAFE_METHODS and path in ADMIN_WRITE_EXACT) or any(path.startswith(prefix) for prefix in ADMIN_PREFIXES):
        return "ADMIN"
    if method not in SAFE_METHODS:
        return "TRADER"
    return "VIEWER"


def _server_endpoint(path: str) -> bool:
    return path.startswith(("/api/", "/ws/")) or path in {"/health", "/docs", "/openapi.json", "/redoc"}


def _valid_host(request: Request, settings: InternalWebSettings) -> bool:
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    return host in {settings.public_hostname, "127.0.0.1", "localhost", "testserver"}


def _loopback(request: Request) -> bool:
    return bool(request.client and request.client.host in {"127.0.0.1", "::1", "testclient"})


def _security_headers(response: Response, settings: InternalWebSettings) -> None:
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' wss:; font-src 'self' data:; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store" if response.headers.get("content-type", "").startswith("application/json") else "no-cache"


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "success": False,
        "data": None,
        "error": {"code": code, "message": "Authentication or authorization failed.", "details": {}},
        "trace_id": None,
    })


async def _asgi_error(send: Send, status: int, code: str) -> None:
    response = _error(status, code)

    async def disconnected() -> Message:
        return {"type": "http.disconnect"}

    await response({"type": "http"}, disconnected, send)
