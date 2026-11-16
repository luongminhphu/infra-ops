from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditLog(Base):
    """Append-only audit trail shared by every module.

    Immutability is enforced in the database (trigger rejects UPDATE/DELETE) and
    tampering is detectable through the ``prev_hash``/``hash`` chain.
    """

    __tablename__ = "core_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_upn: Mapped[str | None] = mapped_column(String(320))
    actor_ip: Mapped[str | None] = mapped_column(String(64))
    module_id: Mapped[str] = mapped_column(String(64), nullable=False)
    system_type: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(256))
    target_label: Mapped[str | None] = mapped_column(String(512))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # success | failure | denied
    message: Mapped[str | None] = mapped_column(String(1024))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    __table_args__ = (
        Index("ix_core_audit_log_at", "at"),
        Index("ix_core_audit_log_target", "target_type", "target_id"),
        Index("ix_core_audit_log_module_action", "module_id", "action"),
    )
