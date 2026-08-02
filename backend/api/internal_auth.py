from __future__ import annotations

from datetime import datetime

import asyncio
import hmac
import json
from collections import defaultdict

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.core.internal_auth import AccessTokenError, LocalPasswordAuthService, load_shared_user
from backend.core.exceptions import AppException
from backend.core.internal_settings import InternalWebSettings, ROLES
from backend.core.responses import success_response
from database.models.internal_auth import InternalAuditEvent, InternalUser
from database.models.workbench import PipelineJob
from database.session import get_auth_session


router = APIRouter(prefix="/api/internal", tags=["internal-auth"])
_sse_connections: dict[int, int] = defaultdict(int)
_sse_guard = asyncio.Lock()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=128)
    role: str
    active: bool


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temporary_password: str = Field(min_length=16, max_length=256)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=16, max_length=256)


@router.get("/auth/me")
def me(request: Request) -> dict:
    user = request.state.internal_user
    settings = _settings(request)
    return success_response(data={
        "authenticated": True,
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "auth_mode": settings.auth_mode,
        "auth_source": "CLOUDFLARE_ACCESS" if request.state.cloudflare_verified else "LOCAL_PASSWORD",
        "identity_scope": "SHARED_IDENTITY" if settings.shared_password_enabled else "VERIFIED_INDIVIDUAL",
        "individual_accountability": not settings.shared_password_enabled,
        "local_password_enabled": settings.local_password_enabled,
        "password_change_required": bool(getattr(request.state, "password_change_required", False)),
    }, trace_id=request.state.trace_id)


@router.get("/auth/config/public")
def public_config(request: Request) -> dict:
    settings = _settings(request)
    return success_response(data={
        "auth_mode": settings.auth_mode,
        "identity_scope": "SHARED_IDENTITY" if settings.shared_password_enabled else "VERIFIED_INDIVIDUAL",
        "individual_accountability": not settings.shared_password_enabled,
        "local_password_enabled": settings.local_password_enabled,
        "force_password_change_on_first_login": settings.force_password_change_on_first_login,
    }, trace_id=request.state.trace_id)


@router.post("/auth/local/login")
@router.post("/auth/login", include_in_schema=False)
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    settings = _settings(request)
    if not settings.local_password_enabled:
        raise AppException(
            "LOCAL_PASSWORD_AUTH_DISABLED",
            "Local password authentication is disabled.",
            status_code=403,
        )
    session = get_auth_session()
    try:
        user = request.state.internal_user
        if settings.shared_password_enabled:
            user = load_shared_user(session, settings, mark_login=False)
            if user is None:
                raise AppException("LOCAL_SHARED_USER_NOT_CONFIGURED", "Local authentication failed.", status_code=401)
        service = LocalPasswordAuthService(session, settings.session_hours)
        try:
            raw_token, csrf, must_change = service.login(user, body.password)
        except AccessTokenError as exc:
            raise AppException(str(exc), "Local authentication failed.", status_code=401) from exc
        response.set_cookie(
            service.cookie_name,
            raw_token,
            max_age=settings.session_hours * 3600,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return success_response(data={
            "authenticated": True, "auth_source": "LOCAL_SHARED_PASSWORD" if settings.shared_password_enabled else "LOCAL_PASSWORD",
            "display_name": user.display_name, "role": user.role, "csrf_token": csrf,
            "password_change_required": False, "redirect_to": "/",
        }, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/auth/set-password")
@router.post("/auth/change-password")
def change_password(body: PasswordChangeRequest, request: Request, response: Response) -> dict:
    session = get_auth_session()
    try:
        settings = _settings(request)
        if settings.shared_password_enabled:
            raise AppException("PASSWORD_CHANGE_NOT_AVAILABLE", "Shared-password changes require the local administrator script.", status_code=403)
        if not settings.local_password_enabled:
            raise AppException("LOCAL_PASSWORD_AUTH_DISABLED", "Local password authentication is disabled.", status_code=403)
        try:
            service = LocalPasswordAuthService(session, settings.session_hours)
            raw_token, csrf = service.change_password(
                request.state.internal_user.id, body.current_password, body.new_password
            )
        except AccessTokenError as exc:
            raise AppException(str(exc), "Password change failed.", status_code=401) from exc
        response.set_cookie(service.cookie_name, raw_token, max_age=settings.session_hours * 3600,
                            secure=True, httponly=True, samesite="lax", path="/")
        return success_response(data={"success": True, "authenticated": True, "changed": True,
                                      "csrf_token": csrf, "must_change_password": False,
                                      "password_change_required": False, "redirect_to": "/"}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    settings = _settings(request)
    session = get_auth_session()
    try:
        service = LocalPasswordAuthService(session, settings.session_hours)
        service.logout(request.cookies.get(service.cookie_name, ""))
        response.delete_cookie(service.cookie_name, path="/", secure=True, httponly=True, samesite="lax")
        return success_response(data={"logged_out": True}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/users")
def users(request: Request) -> dict:
    session = get_auth_session()
    try:
        rows = list(session.scalars(select(InternalUser).order_by(InternalUser.email)))
        return success_response(data={"items": [_user(row) for row in rows]}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.put("/users/{user_id}")
def update_user(user_id: int, body: UserUpdateRequest, request: Request) -> dict:
    role = body.role.upper()
    if role not in ROLES:
        raise ValueError("INVALID_INTERNAL_ROLE")
    session = get_auth_session()
    try:
        row = session.get(InternalUser, user_id)
        if row is None:
            raise ValueError("INTERNAL_USER_NOT_FOUND")
        row.display_name, row.role, row.active = body.display_name, role, body.active
        session.commit()
        return success_response(data=_user(row), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/users/{user_id}/password-reset")
def reset_password(user_id: int, body: PasswordResetRequest, request: Request) -> dict:
    session = get_auth_session()
    try:
        if session.get(InternalUser, user_id) is None:
            raise ValueError("INTERNAL_USER_NOT_FOUND")
        LocalPasswordAuthService(session, _settings(request).session_hours).set_temporary_password(
            user_id, body.temporary_password
        )
        return success_response(data={"reset": True, "must_change_password": True}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/audit")
def audit(request: Request, limit: int = 100) -> dict:
    session = get_auth_session()
    try:
        rows = list(session.scalars(select(InternalAuditEvent).order_by(InternalAuditEvent.created_at.desc()).limit(min(500, max(1, limit)))))
        return success_response(data={"items": [{
            "access_email": row.access_email,
            "role": row.role,
            "operation": row.operation,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "job_id": row.job_id,
            "created_at": _iso(row.created_at),
        } for row in rows]}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/events/jobs")
async def job_events(request: Request) -> StreamingResponse:
    user_id = request.state.internal_user.id
    async with _sse_guard:
        if _sse_connections[user_id] >= 2:
            raise ValueError("SSE_CONNECTION_LIMIT_REACHED")
        _sse_connections[user_id] += 1

    async def stream():
        try:
            last_payload = ""
            while not await request.is_disconnected():
                session = get_auth_session()
                try:
                    rows = list(session.scalars(select(PipelineJob).order_by(PipelineJob.created_at.desc()).limit(20)))
                    payload = json.dumps([{
                        "job_id": row.job_id,
                        "job_type": row.job_type,
                        "trade_date": row.trade_date.isoformat(),
                        "status": row.status,
                        "stage": row.stage,
                        "progress_current": row.progress_current,
                        "progress_total": row.progress_total,
                    } for row in rows], ensure_ascii=False)
                finally:
                    session.close()
                if payload != last_payload:
                    yield f"event: jobs\ndata: {payload}\n\n"
                    last_payload = payload
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(2)
        finally:
            async with _sse_guard:
                _sse_connections[user_id] = max(0, _sse_connections[user_id] - 1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _user(row: InternalUser) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "display_name": row.display_name,
        "role": row.role,
        "active": row.active,
        "last_login_at": _iso(row.last_login_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _settings(request: Request) -> InternalWebSettings:
    return request.app.state.internal_settings
