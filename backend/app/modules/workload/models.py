from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Technician(Base):
    """An SDP technician, optionally linked to an infra-ops user (the Operator)."""

    __tablename__ = "workload_technician"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # SDP technician id
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(320))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_user.id", ondelete="SET NULL"), unique=True
    )
    # True when an admin linked the user by hand (automatic e-mail matching then leaves it alone).
    mapped_manually: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Ticket(Base):
    """Snapshot of an SDP request (Helpdesk/Support task)."""

    __tablename__ = "workload_ticket"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # SDP request id
    display_id: Mapped[str | None] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    requester_name: Mapped[str | None] = mapped_column(String(256))
    technician_id: Mapped[str | None] = mapped_column(String(32), index=True)
    technician_name: Mapped[str | None] = mapped_column(String(256))
    group_name: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # open | done | cancelled (from the status names in settings)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    priority: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    due_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sdp_updated_ms: Mapped[int | None] = mapped_column(BigInteger, index=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
