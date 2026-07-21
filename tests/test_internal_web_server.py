from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from backend.application.workflow import WorkflowApplicationService
from backend.core.internal_auth import (
    AccessTokenError,
    AuthenticatedUser,
    CloudflareAccessVerifier,
    LocalPasswordAuthService,
    sync_internal_users,
)
from backend.core.internal_settings import InternalWebSettings
from backend.core.process_lock import ProcessFileLock, SingleInstanceError
from backend.internal_web_entry import create_internal_web_app
from backend.internal_web_service import _assign_to_job, _create_kill_on_close_job, _terminate_process_tree
from database.base import Base
from database.models.internal_auth import (
    InternalAuditEvent,
    InternalPasswordCredential,
    InternalUser,
    JobExecutionLock,
)
from database.models.workbench import PipelineJob
from database.session import close_db, create_engine_from_url, get_engine, get_session, init_db


PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
USERS = {
    "admin@example.com": "ADMIN",
    "trader@example.com": "TRADER",
    "viewer@example.com": "VIEWER",
    "viewer2@example.com": "VIEWER",
}


class KeyCache:
    def __init__(self, key=PRIVATE_KEY.public_key(), *, fail=False):
        self.key_value = key
        self.fail = fail
        self.kids = []

    async def key(self, kid):
        self.kids.append(kid)
        if self.fail:
            raise AccessTokenError("ACCESS_JWKS_UNAVAILABLE")
        return self.key_value


class ClaimsVerifier:
    def __init__(self, email): self.email = email
    async def verify(self, assertion):
        if not assertion:
            raise AccessTokenError("ACCESS_TOKEN_MISSING")
        return {"email": self.email}


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def internal_env(tmp_path: Path, monkeypatch):
    close_db()
    monkeypatch.setenv("AI_TRADER_DB_PATH", str(tmp_path / "internal.db"))
    monkeypatch.setenv("APP_RUNTIME_MODE", "INTERNAL_WEB_SERVER")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_LOCAL_AUTH_BYPASS", "false")
    monkeypatch.setenv("APP_PUBLIC_HOSTNAME", "trader.example.com")
    monkeypatch.setenv("CLOUDFLARE_TEAM_DOMAIN", "team.cloudflareaccess.com")
    monkeypatch.setenv("CLOUDFLARE_ACCESS_AUD", "aud-test")
    monkeypatch.setenv("ALLOWED_USER_EMAILS", ",".join(USERS))
    monkeypatch.setenv("INTERNAL_USER_ROLES", json.dumps(USERS))
    monkeypatch.setenv("AI_TRADER_SERVER_DATA_ROOT", str(tmp_path / "programdata"))
    yield tmp_path
    close_db()


def settings(*, bypass=False, auth_mode="CLOUDFLARE_ACCESS", max_bytes=1024 * 1024):
    return InternalWebSettings(
        runtime_mode="INTERNAL_WEB_SERVER", app_env="development",
        public_hostname="trader.example.com", origin_host="127.0.0.1", origin_port=8080,
        team_domain="team.cloudflareaccess.com", access_aud="aud-test", auth_mode=auth_mode,
        allowed_users=USERS, session_hours=12, local_bypass=bypass, max_request_bytes=max_bytes,
    )


def shared_settings(*, app_env="development"):
    return InternalWebSettings(
        runtime_mode="INTERNAL_WEB_SERVER", app_env=app_env,
        public_hostname="trader.example.com", origin_host="127.0.0.1", origin_port=8080,
        team_domain="", access_aud="", auth_mode="LOCAL_SHARED_PASSWORD",
        allowed_users={"shared-partners@local.invalid": "ADMIN"}, session_hours=12,
        local_bypass=False, max_request_bytes=1024 * 1024,
        shared_login_username="partners",
    )


def token(**overrides):
    now = datetime.now(timezone.utc)
    key = overrides.pop("_key", PRIVATE_KEY)
    payload = {
        "iss": "https://team.cloudflareaccess.com", "aud": ["aud-test"],
        "email": "admin@example.com", "iat": now, "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
    } | overrides
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "kid-1"})


@pytest.mark.anyio
async def test_access_jwt_validates_signature_issuer_audience_time_and_email():
    verifier = CloudflareAccessVerifier(settings(), KeyCache())
    assert (await verifier.verify(token()))["email"] == "admin@example.com"
    cases = [
        (token(_key=OTHER_KEY), "ACCESS_TOKEN_INVALID"),
        (token(iss="https://wrong.cloudflareaccess.com"), "ACCESS_ISSUER_INVALID"),
        (token(aud=["wrong"]), "ACCESS_AUDIENCE_INVALID"),
        (token(exp=datetime.now(timezone.utc) - timedelta(minutes=1)), "ACCESS_TOKEN_EXPIRED"),
        (token(nbf=datetime.now(timezone.utc) + timedelta(minutes=2)), "ACCESS_TOKEN_NOT_ACTIVE"),
        (token(email=""), "ACCESS_EMAIL_MISSING"),
    ]
    for value, expected in cases:
        with pytest.raises(AccessTokenError, match=expected):
            await verifier.verify(value)


@pytest.mark.anyio
async def test_jwks_failure_is_fail_closed_and_kid_is_used_for_rotation():
    failing = CloudflareAccessVerifier(settings(), KeyCache(fail=True))
    with pytest.raises(AccessTokenError, match="ACCESS_JWKS_UNAVAILABLE"):
        await failing.verify(token())
    cache = KeyCache()
    await CloudflareAccessVerifier(settings(), cache).verify(token())
    assert cache.kids == ["kid-1"]


def test_static_spa_api_security_headers_and_single_origin(internal_env: Path):
    dist = internal_env / "dist"; (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>internal-workbench</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    app = create_internal_web_app(
        settings(bypass=True), frontend_dist=dist, lock_path=internal_env / "server.lock"
    )
    with TestClient(app, base_url="https://trader.example.com") as client:
        page = client.get("/selection-performance")
        assert page.status_code == 200 and "internal-workbench" in page.text
        assert page.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert client.get("/assets/app.js").status_code == 200
        api = client.get("/api/does-not-exist")
        assert api.status_code == 404 and "text/html" not in api.headers.get("content-type", "")
        assert client.get("/workbench", headers={"host": "evil.example"}).status_code == 400


def test_authentication_roles_inactive_user_and_audit(internal_env: Path):
    dist = internal_env / "dist"; dist.mkdir(); (dist / "index.html").write_text("ok", encoding="utf-8")
    viewer_app = create_internal_web_app(settings(), frontend_dist=dist, lock_path=internal_env / "viewer.lock", auth_verifier=ClaimsVerifier("viewer@example.com"))
    with TestClient(viewer_app) as client:
        headers = {"Cf-Access-Jwt-Assertion": "verified-at-edge"}
        assert client.get("/api/internal/auth/me", headers=headers).json()["data"]["role"] == "VIEWER"
        assert client.put("/api/workbench/settings", headers=headers, json={"values": {}}).status_code == 403
        assert client.post("/api/workbench/midday/run", headers=headers, json={}).status_code == 403

    trader_app = create_internal_web_app(settings(), frontend_dist=dist, lock_path=internal_env / "trader.lock", auth_verifier=ClaimsVerifier("trader@example.com"))
    with TestClient(trader_app) as client:
        headers = {"Cf-Access-Jwt-Assertion": "verified-at-edge"}
        assert client.post("/api/internal/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/internal/users", headers=headers).status_code == 403
    session = get_session()
    try:
        assert session.scalar(select(func.count()).select_from(InternalAuditEvent)) >= 1
        user = session.scalar(select(InternalUser).where(InternalUser.email == "viewer2@example.com"))
        user.active = False; session.commit()
    finally: session.close()
    inactive_app = create_internal_web_app(settings(), frontend_dist=dist, lock_path=internal_env / "inactive.lock", auth_verifier=ClaimsVerifier("viewer2@example.com"))
    with TestClient(inactive_app) as client:
        assert client.get("/workbench", headers={"Cf-Access-Jwt-Assertion": "x"}).status_code == 403
    unknown_app = create_internal_web_app(settings(), frontend_dist=dist, lock_path=internal_env / "unknown.lock", auth_verifier=ClaimsVerifier("unknown@example.com"))
    with TestClient(unknown_app) as client:
        assert client.get("/workbench", headers={"Cf-Access-Jwt-Assertion": "x"}).status_code == 403


def test_user_sync_deactivates_removed_accounts_without_reactivating_admin_disabled_user(internal_env: Path):
    init_db()
    session = get_session()
    try:
        sync_internal_users(session, settings())
        configured = session.scalar(select(InternalUser).where(InternalUser.email == "viewer@example.com"))
        configured.active = False
        removed = InternalUser(email="removed@example.com", display_name="Removed", role="TRADER", active=True)
        session.add(removed)
        session.commit()
        service = LocalPasswordAuthService(session, 12)
        service.set_temporary_password(removed.id, "Temporary-Password-123")
        raw, _, _ = service.login(AuthenticatedUser(removed.id, removed.email, removed.display_name, removed.role), "Temporary-Password-123")
        sync_internal_users(session, settings())
        session.refresh(removed)
        session.refresh(configured)
        assert removed.active is False
        assert configured.active is False
        assert service.validate(raw) is None
    finally:
        session.close()


def test_request_body_limit_and_missing_auth(internal_env: Path):
    dist = internal_env / "dist"; dist.mkdir(); (dist / "index.html").write_text("ok", encoding="utf-8")
    app = create_internal_web_app(settings(max_bytes=128), frontend_dist=dist, lock_path=internal_env / "limit.lock", auth_verifier=ClaimsVerifier("admin@example.com"))
    with TestClient(app) as client:
        assert client.get("/workbench").status_code == 401
        response = client.post("/api/workbench/data/check", headers={"Cf-Access-Jwt-Assertion": "x"}, content=b"x" * 129)
        assert response.status_code == 413
        chunked = client.post(
            "/api/workbench/data/check",
            headers={"Cf-Access-Jwt-Assertion": "x"},
            content=(chunk for chunk in (b"x" * 80, b"y" * 80)),
        )
        assert chunked.status_code == 413


def test_process_lock_rejects_second_instance_and_recovers_stale(tmp_path: Path):
    path = tmp_path / "server.lock"
    first = ProcessFileLock(path); first.acquire()
    with pytest.raises(SingleInstanceError, match="ALREADY_RUNNING"):
        ProcessFileLock(path).acquire()
    first.release()
    path.write_text('{"pid": 99999999}', encoding="utf-8")
    recovered = ProcessFileLock(path); recovered.acquire(); recovered.release()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-only")
def test_windows_job_object_stops_packaged_process_tree():
    script = (
        "import subprocess,sys,time; time.sleep(1); "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(p.pid,flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    job = _create_kill_on_close_job()
    child_pid = None
    try:
        _assign_to_job(job, process.pid)
        child_pid = int(process.stdout.readline().strip())
        _terminate_process_tree(process, job)
        assert process.poll() is not None
        with pytest.raises(OSError):
            os.kill(child_pid, 0)
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)


def test_sqlite_wal_busy_timeout_and_atomic_duplicate_job_lock(tmp_path: Path):
    database = tmp_path / "jobs.db"
    engine = create_engine_from_url(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() >= 30_000

    barrier = threading.Barrier(2); results = []
    def start(email):
        session = get_session(engine)
        try:
            barrier.wait()
            results.append(WorkflowApplicationService(session).start(
                "QUANT", date(2026, 7, 17), {}, actor={"email": email, "role": "TRADER"}
            ))
        finally: session.close()
    threads = [threading.Thread(target=start, args=(f"trader{i}@example.com",)) for i in range(2)]
    [item.start() for item in threads]; [item.join() for item in threads]
    session = get_session(engine)
    try:
        assert len({item["job_id"] for item in results}) == 1
        assert session.scalar(select(func.count()).select_from(PipelineJob)) == 1
        assert session.scalar(select(func.count()).select_from(JobExecutionLock)) == 1
        assert {item["duplicate_status"] for item in results} == {"NEW_JOB", "ACTIVE_JOB_REUSED"}
    finally: session.close(); engine.dispose()


def test_argon2_password_lock_csrf_and_logout(tmp_path: Path):
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'password.db').as_posix()}")
    Base.metadata.create_all(engine); session = get_session(engine)
    try:
        user = InternalUser(email="admin@example.com", display_name="Admin", role="ADMIN", active=True)
        session.add(user); session.commit()
        service = LocalPasswordAuthService(session, 12)
        service.set_temporary_password(user.id, "Temporary-Password-123")
        raw, csrf, must_change = service.login(AuthenticatedUser(user.id, user.email, user.display_name, user.role), "Temporary-Password-123")
        assert must_change is True and service.validate(raw, csrf) is not None
        assert service.validate(raw, "wrong") is None
        service.set_temporary_password(user.id, "Another-Temporary-123")
        assert service.validate(raw) is None
        raw, csrf, _ = service.login(AuthenticatedUser(user.id, user.email, user.display_name, user.role), "Another-Temporary-123")
        service.change_password(user.id, "Another-Temporary-123", "New-Password-456!")
        service.logout(raw)
        assert service.validate(raw) is None
        for _ in range(5):
            with pytest.raises(AccessTokenError):
                service.login(AuthenticatedUser(user.id, user.email, user.display_name, user.role), "wrong")
        with pytest.raises(AccessTokenError, match="LOCKED"):
            service.login(AuthenticatedUser(user.id, user.email, user.display_name, user.role), "New-Password-456!")
    finally: session.close(); engine.dispose()


def test_local_password_requires_matching_user_csrf_and_first_change(internal_env: Path):
    dist = internal_env / "dist"; dist.mkdir(); (dist / "index.html").write_text("ok", encoding="utf-8")
    password_settings = settings(auth_mode="CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD")
    app = create_internal_web_app(
        password_settings,
        frontend_dist=dist,
        lock_path=internal_env / "password.lock",
        auth_verifier=ClaimsVerifier("viewer@example.com"),
    )
    with TestClient(app, base_url="https://trader.example.com") as client:
        session = get_session()
        try:
            viewer = session.scalar(select(InternalUser).where(InternalUser.email == "viewer@example.com"))
            LocalPasswordAuthService(session, 12).set_temporary_password(viewer.id, "Temporary-Password-123")
        finally:
            session.close()
        headers = {"Cf-Access-Jwt-Assertion": "edge-verified"}
        assert client.get("/local-login", headers=headers).status_code == 200
        assert client.get("/api/internal/auth/me", headers=headers).status_code == 401
        logged_in = client.post("/api/internal/auth/login", headers=headers, json={"password": "Temporary-Password-123"})
        assert logged_in.status_code == 200
        csrf = logged_in.json()["data"]["csrf_token"]
        assert client.get("/api/internal/auth/me", headers=headers).status_code == 403
        assert client.post("/api/internal/auth/logout", headers=headers).status_code == 403
        changed = client.post(
            "/api/internal/auth/change-password",
            headers=headers | {"X-CSRF-Token": csrf},
            json={"current_password": "Temporary-Password-123", "new_password": "New-Password-456!"},
        )
        assert changed.status_code == 200
        assert client.get("/api/internal/auth/me", headers=headers).status_code == 200
        assert client.post("/api/internal/auth/logout", headers=headers | {"X-CSRF-Token": csrf}).status_code == 200


def test_shared_password_mode_login_session_csrf_and_no_cloudflare_dependency(internal_env: Path):
    dist = internal_env / "dist"; dist.mkdir(); (dist / "index.html").write_text("shared-login", encoding="utf-8")
    app = create_internal_web_app(
        shared_settings(), frontend_dist=dist, lock_path=internal_env / "shared.lock"
    )
    with TestClient(app, base_url="https://trader.example.com") as client:
        assert client.get("/workbench").status_code == 200
        assert client.get("/api/internal/auth/me").status_code == 401
        session = get_session()
        try:
            user = session.scalar(select(InternalUser).where(InternalUser.email == "shared-partners@local.invalid"))
            assert user is not None and user.role == "ADMIN"
            LocalPasswordAuthService(session, 12).set_password(user.id, "Shared-Password-123!", must_change=False)
        finally:
            session.close()

        assert client.post(
            "/api/internal/auth/login", json={"username": "wrong", "password": "Shared-Password-123!"}
        ).status_code == 401
        logged_in = client.post(
            "/api/internal/auth/login", json={"username": "partners", "password": "Shared-Password-123!"}
        )
        assert logged_in.status_code == 200
        csrf = logged_in.json()["data"]["csrf_token"]
        identity = client.get("/api/internal/auth/me").json()["data"]
        assert identity["auth_mode"] == "LOCAL_SHARED_PASSWORD" and identity["role"] == "ADMIN"
        assert client.post("/api/internal/auth/logout").status_code == 403
        assert client.post("/api/internal/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/api/internal/auth/me").status_code == 401


def test_shared_password_production_settings_do_not_require_cloudflare_access():
    shared_settings(app_env="production").validate_production()
