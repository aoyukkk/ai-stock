from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin, utc_now


class InternalUser(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "internal_user"
    __table_args__ = (
        UniqueConstraint("email", name="uq_internal_user_email"),
        Index("ix_internal_user_role_active", "role", "active"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InternalPasswordCredential(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "internal_password_credential"
    __table_args__ = (UniqueConstraint("internal_user_id", name="uq_internal_password_user"),)

    internal_user_id: Mapped[int] = mapped_column(nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InternalAuthSession(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "internal_auth_session"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_internal_auth_session_token"),
        Index("ix_internal_auth_session_user_active", "internal_user_id", "revoked_at"),
    )

    internal_user_id: Mapped[int] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InternalAuditEvent(IDMixin, ReprMixin, Base):
    __tablename__ = "internal_audit_event"
    __table_args__ = (
        Index("ix_internal_audit_created", "created_at"),
        Index("ix_internal_audit_user_operation", "internal_user_id", "operation"),
    )

    access_email: Mapped[str] = mapped_column(String(320), nullable=False)
    internal_user_id: Mapped[int] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent_summary: Mapped[str | None] = mapped_column(String(256))
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, default="http_request")
    entity_id: Mapped[str | None] = mapped_column(String(128))
    job_id: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class JobExecutionLock(IDMixin, ReprMixin, Base):
    __tablename__ = "job_execution_lock"
    __table_args__ = (UniqueConstraint("lock_key", name="uq_job_execution_lock_key"),)

    lock_key: Mapped[str] = mapped_column(String(160), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    started_by: Mapped[str] = mapped_column(String(320), nullable=False)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="QUEUED")
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
