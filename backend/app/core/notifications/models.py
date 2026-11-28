from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk


class NotificationPref(Base):
    """A person's opt-in to the e-mail digest (off until they switch it on)."""

    __tablename__ = "core_notification_pref"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_user.id", ondelete="CASCADE"), primary_key=True
    )
    email_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationSent(UUIDPk, Base):
    """One digest per (person, channel, slot). Written and committed BEFORE sending: a pending row left by a
    crash is never re-sent. ``user_id`` is NULL for the team-wide Teams message."""

    __tablename__ = "core_notification_sent"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_user.id", ondelete="CASCADE")
    )
    slot: Mapped[str] = mapped_column(String(16), nullable=False)  # "2026-11-28T08:00", VN time
    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # email | teams
    item_key: Mapped[str | None] = mapped_column(String(256))
    priority: Mapped[int | None] = mapped_column(Integer)  # most urgent priority in the digest
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("user_id", "channel", "slot", name="uq_core_notification_sent_user_channel_slot"),
        Index(
            "uq_core_notification_sent_team_slot",
            "channel",
            "slot",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
    )


class NotificationItem(Base):
    """Last priority at which an item was e-mailed to a person: it is repeated when it gets more urgent or its title changes."""

    __tablename__ = "core_notification_item"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_user.id", ondelete="CASCADE"), primary_key=True
    )
    item_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    last_priority: Mapped[int] = mapped_column(Integer, nullable=False)
    last_title: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
