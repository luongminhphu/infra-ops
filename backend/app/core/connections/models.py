from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, Timestamps, UUIDPk


class ConnectionProfile(UUIDPk, Timestamps, Base):
    """Connection to one external system (SDP, Entra, AD, BioStar…), configured at runtime by admins."""

    __tablename__ = "core_connection_profile"

    system_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="prod")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Non-secret settings validated against the connector's config schema.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Optimistic concurrency: clients send the version they edited.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    credentials: Mapped[list[Credential]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (CheckConstraint("environment in ('prod','test')", name="environment"),)


class Credential(UUIDPk, Timestamps, Base):
    """Secret material for a profile, one row per purpose (read / write).

    ``public`` holds non-secret identifiers (username, client id) that may be shown.
    ``ciphertext`` is AES-256-GCM over the JSON of the secret fields; never returned by the API.
    """

    __tablename__ = "core_credential"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_connection_profile.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(48), nullable=False)
    public: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(String(16), nullable=False)
    hint: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    profile: Mapped[ConnectionProfile] = relationship(back_populates="credentials")

    __table_args__ = (
        UniqueConstraint("profile_id", "purpose", name="uq_core_credential_profile_purpose"),
        CheckConstraint("purpose in ('read','write')", name="purpose"),
    )


class UserCredential(UUIDPk, Timestamps, Base):
    """A person's own credential for a profile (e.g. their SDP technician API key).

    Work a person is responsible for (their Jobs, receiving goods, reassigning tasks) reads and writes the
    external system with it, so the system's own audit shows who did what. Encrypted like ``Credential``
    (AAD bound to user + profile); the secret is never returned or shown to anyone, admins included.
    """

    __tablename__ = "core_user_credential"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_connection_profile.id", ondelete="CASCADE"), nullable=False
    )
    auth_type: Mapped[str] = mapped_column(String(48), nullable=False)
    public: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(String(16), nullable=False)
    hint: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (UniqueConstraint("user_id", "profile_id", name="uq_core_user_credential_user_profile"),)
