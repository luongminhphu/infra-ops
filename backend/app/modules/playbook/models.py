"""Infra Playbook: guides drawn as branching mind maps. A guide keeps one editable draft and its published versions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Guide(Base):
    __tablename__ = "playbook_guide"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    description: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")
    # The working copy editors change: {"root": id, "nodes": {id: {title, note, children: [{label, to}]}}}
    draft: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    draft_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    draft_updated_by: Mapped[str | None] = mapped_column(String(320))
    published_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(320))


class GuideVersion(Base):
    """A published, immutable copy (what IT Support reads; kept for the ISO change history)."""

    __tablename__ = "playbook_guide_version"
    __table_args__ = (UniqueConstraint("guide_id", "version", name="uq_playbook_guide_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guide_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbook_guide.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    tree: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_by: Mapped[str | None] = mapped_column(String(320))


class GuideImage(Base):
    """A screenshot pasted into a step's note (the bytes live in core storage)."""

    __tablename__ = "playbook_image"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guide_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbook_guide.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core_file.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(320))


class JobRun(Base):
    """A Playbook pinned to one Job (guide + version fixed at the first write) with the choices and the frozen
    check/switch decisions made for it."""

    __tablename__ = "playbook_job_run"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_job.id", ondelete="CASCADE"), primary_key=True
    )
    guide_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbook_guide.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    choices: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    decisions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    pinned_by: Mapped[str | None] = mapped_column(String(320))


class JobMark(Base):
    """A manual step of the pinned map ticked (done) or set aside (na, with a reason) for a Job."""

    __tablename__ = "playbook_job_mark"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbook_job_run.job_id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False)  # done | na
    reason: Mapped[str | None] = mapped_column(String(500))
    done_by: Mapped[str | None] = mapped_column(String(320))
    done_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
