from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select

from backend.core.internal_settings import InternalWebSettings
from database.models.internal_auth import (
    InternalAuthSession,
    InternalPasswordCredential,
    InternalUser,
)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    email: str
    display_name: str
    role: str


class AccessTokenError(RuntimeError):
    pass


class CloudflareJwksCache:
    def __init__(self, team_domain: str, *, ttl_seconds: int = 300) -> None:
        self.url = f"https://{team_domain}/cdn-cgi/access/certs"
        self.ttl_seconds = ttl_seconds
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0

    async def key(self, kid: str) -> Any:
        if time.monotonic() >= self._expires_at or kid not in self._keys:
            await self.refresh()
        key = self._keys.get(kid)
        if key is None:
            await self.refresh()
            key = self._keys.get(kid)
        if key is None:
            raise AccessTokenError("ACCESS_SIGNING_KEY_NOT_FOUND")
        return key

    async def refresh(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(self.url)
                response.raise_for_status()
                payload = response.json()
            keys = payload.get("keys") or []
            parsed = {
                str(item["kid"]): jwt.PyJWK.from_dict(item).key
                for item in keys
                if isinstance(item, dict) and item.get("kid")
            }
            if not parsed:
                raise AccessTokenError("ACCESS_JWKS_EMPTY")
            self._keys = parsed
            self._expires_at = time.monotonic() + self.ttl_seconds
        except AccessTokenError:
            raise
        except Exception as exc:
            raise AccessTokenError("ACCESS_JWKS_UNAVAILABLE") from exc


class CloudflareAccessVerifier:
    def __init__(self, settings: InternalWebSettings, jwks: CloudflareJwksCache | None = None) -> None:
        self.settings = settings
        self.jwks = jwks or CloudflareJwksCache(settings.team_domain)

    async def verify(self, token: str) -> dict[str, Any]:
        if not token or len(token) > 16_384:
            raise AccessTokenError("ACCESS_TOKEN_MISSING")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise AccessTokenError("ACCESS_TOKEN_HEADER_INVALID")
            key = await self.jwks.key(str(header["kid"]))
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.settings.access_aud,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iss", "aud", "email"]},
                leeway=30,
            )
            email = str(claims.get("email") or "").strip().lower()
            if not email:
                raise AccessTokenError("ACCESS_EMAIL_MISSING")
            return claims | {"email": email}
        except AccessTokenError:
            raise
        except jwt.ExpiredSignatureError as exc:
            raise AccessTokenError("ACCESS_TOKEN_EXPIRED") from exc
        except jwt.ImmatureSignatureError as exc:
            raise AccessTokenError("ACCESS_TOKEN_NOT_ACTIVE") from exc
        except jwt.InvalidAudienceError as exc:
            raise AccessTokenError("ACCESS_AUDIENCE_INVALID") from exc
        except jwt.InvalidIssuerError as exc:
            raise AccessTokenError("ACCESS_ISSUER_INVALID") from exc
        except jwt.PyJWTError as exc:
            raise AccessTokenError("ACCESS_TOKEN_INVALID") from exc


def sync_internal_users(session, settings: InternalWebSettings) -> None:
    if settings.shared_password_enabled:
        row = session.scalar(select(InternalUser).where(InternalUser.user_key == "shared_internal_user"))
        if row is None:
            row = session.scalar(select(InternalUser).where(InternalUser.email == settings.shared_identity_email))
        if row is None:
            session.add(InternalUser(
                email=settings.shared_identity_email,
                user_key="shared_internal_user",
                display_name="内部共享用户",
                role=settings.shared_identity_role,
                active=True,
            ))
        else:
            row.user_key = "shared_internal_user"
            row.display_name = "内部共享用户"
            row.role = settings.shared_identity_role
            row.active = True
        session.commit()
        return
    configured = set(settings.allowed_users)
    now = datetime.now(timezone.utc)
    for row in session.scalars(select(InternalUser)):
        if row.email not in configured and row.active:
            row.active = False
            for auth_session in session.scalars(select(InternalAuthSession).where(
                InternalAuthSession.internal_user_id == row.id,
                InternalAuthSession.revoked_at.is_(None),
            )):
                auth_session.revoked_at = now
    for email, role in settings.allowed_users.items():
        row = session.scalar(select(InternalUser).where(InternalUser.email == email))
        if row is None:
            display_name = "合伙人共享账号" if email == settings.shared_identity_email else email.split("@", 1)[0]
            session.add(InternalUser(email=email, display_name=display_name, role=role, active=True))
        else:
            row.role = role
    session.commit()


def load_active_user(session, email: str, *, mark_login: bool = True) -> AuthenticatedUser | None:
    row = session.scalar(select(InternalUser).where(InternalUser.email == email.lower()))
    if row is None or not row.active:
        return None
    now = datetime.now(timezone.utc)
    if mark_login and (row.last_login_at is None or _aware(row.last_login_at) < now - timedelta(minutes=15)):
        row.last_login_at = now
        session.commit()
    return AuthenticatedUser(row.id, row.email, row.display_name, row.role)


def load_shared_user(session, settings: InternalWebSettings, *, mark_login: bool = True) -> AuthenticatedUser | None:
    row = session.scalar(select(InternalUser).where(InternalUser.user_key == "shared_internal_user"))
    if row is None:
        row = session.scalar(select(InternalUser).where(InternalUser.email == settings.shared_identity_email))
    if row is None or not row.active:
        return None
    now = datetime.now(timezone.utc)
    if mark_login and (row.last_login_at is None or _aware(row.last_login_at) < now - timedelta(minutes=15)):
        row.last_login_at = now
        session.commit()
    return AuthenticatedUser(row.id, row.email, row.display_name, row.role)


class LocalPasswordAuthService:
    cookie_name = "ai_trader_internal_session"

    def __init__(self, session, session_hours: int) -> None:
        self.session = session
        self.session_hours = session_hours
        self.hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

    def set_temporary_password(self, user_id: int, password: str, *, must_change: bool = False) -> None:
        self.set_password(user_id, password, must_change=must_change)

    def set_password(self, user_id: int, password: str, *, must_change: bool = False) -> None:
        if len(password) < 16:
            raise ValueError("PASSWORD_TOO_SHORT")
        row = self.session.scalar(select(InternalPasswordCredential).where(
            InternalPasswordCredential.internal_user_id == user_id
        ))
        password_hash = self.hasher.hash(password)
        if row is None:
            row = InternalPasswordCredential(
                internal_user_id=user_id,
                password_hash=password_hash,
                must_change_password=must_change,
                password_initialized=True,
                password_changed_at=datetime.now(timezone.utc),
                session_version=1,
                failed_attempts=0,
            )
            self.session.add(row)
        else:
            row.password_hash = password_hash
            row.must_change_password = must_change
            row.password_initialized = True
            row.password_changed_at = datetime.now(timezone.utc)
            row.session_version += 1
            row.failed_attempts = 0
            row.locked_until = None
        now = datetime.now(timezone.utc)
        for active_session in self.session.scalars(select(InternalAuthSession).where(
            InternalAuthSession.internal_user_id == user_id,
            InternalAuthSession.revoked_at.is_(None),
        )):
            active_session.revoked_at = now
        self.session.commit()

    def login(self, user: AuthenticatedUser, password: str) -> tuple[str, str, bool]:
        credential = self.session.scalar(select(InternalPasswordCredential).where(
            InternalPasswordCredential.internal_user_id == user.id
        ))
        now = datetime.now(timezone.utc)
        if credential is None:
            raise AccessTokenError("LOCAL_PASSWORD_NOT_CONFIGURED")
        if credential.locked_until and _aware(credential.locked_until) > now:
            raise AccessTokenError("LOCAL_PASSWORD_LOCKED")
        try:
            self.hasher.verify(credential.password_hash, password)
        except VerifyMismatchError as exc:
            credential.failed_attempts += 1
            if credential.failed_attempts >= 5:
                credential.locked_until = now + timedelta(minutes=15)
            self.session.commit()
            raise AccessTokenError("LOCAL_PASSWORD_INVALID") from exc
        credential.failed_attempts = 0
        credential.locked_until = None
        internal_user = self.session.get(InternalUser, user.id)
        if internal_user is not None:
            internal_user.last_login_at = now
        raw_token, csrf = self._create_session(user.id, now)
        self.session.commit()
        return raw_token, csrf, credential.must_change_password

    def change_password(self, user_id: int, current_password: str, new_password: str) -> tuple[str, str]:
        if len(new_password) < 16 or hmac.compare_digest(current_password, new_password):
            raise ValueError("NEW_PASSWORD_INVALID")
        credential = self.session.scalar(select(InternalPasswordCredential).where(
            InternalPasswordCredential.internal_user_id == user_id
        ))
        if credential is None:
            raise AccessTokenError("LOCAL_PASSWORD_NOT_CONFIGURED")
        try:
            self.hasher.verify(credential.password_hash, current_password)
        except VerifyMismatchError as exc:
            raise AccessTokenError("LOCAL_PASSWORD_INVALID") from exc
        credential.password_hash = self.hasher.hash(new_password)
        credential.must_change_password = False
        credential.password_initialized = True
        credential.password_changed_at = datetime.now(timezone.utc)
        credential.session_version += 1
        credential.failed_attempts = 0
        credential.locked_until = None
        now = datetime.now(timezone.utc)
        for active_session in self.session.scalars(select(InternalAuthSession).where(
            InternalAuthSession.internal_user_id == user_id,
            InternalAuthSession.revoked_at.is_(None),
        )):
            active_session.revoked_at = now
        raw_token, csrf = self._create_session(user_id, now)
        self.session.commit()
        return raw_token, csrf

    def _create_session(self, user_id: int, now: datetime | None = None) -> tuple[str, str]:
        raw_token = secrets.token_urlsafe(48)
        csrf = secrets.token_urlsafe(32)
        self.session.add(InternalAuthSession(
            internal_user_id=user_id,
            token_hash=_hash(raw_token),
            csrf_hash=_hash(csrf),
            expires_at=(now or datetime.now(timezone.utc)) + timedelta(hours=self.session_hours),
        ))
        return raw_token, csrf

    def validate(self, raw_token: str, csrf: str | None = None) -> InternalAuthSession | None:
        if not raw_token:
            return None
        row = self.session.scalar(select(InternalAuthSession).where(
            InternalAuthSession.token_hash == _hash(raw_token)
        ))
        now = datetime.now(timezone.utc)
        if row is None or row.revoked_at is not None or _aware(row.expires_at) <= now:
            return None
        if csrf is not None and not hmac.compare_digest(row.csrf_hash, _hash(csrf)):
            return None
        return row

    def logout(self, raw_token: str) -> None:
        row = self.validate(raw_token)
        if row is not None:
            row.revoked_at = datetime.now(timezone.utc)
            self.session.commit()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
