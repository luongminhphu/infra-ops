from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPk


class User(UUIDPk, Timestamps, Base):
    """A person allowed to sign in. Created on first SSO login or pre-created by an admin."""

    __tablename__ = "core_user"

    entra_oid: Mapped[str | None] = mapped_column(String(64), unique=True)
    upn: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(320))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Local accounts only (interim login until Entra SSO is configured). Null for SSO users.
    password_hash: Mapped[str | None] = mapped_column(String(256))
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    """Server-side session. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "core_session"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
