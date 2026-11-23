from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, Timestamps, UUIDPk


class Job(UUIDPk, Timestamps, Base):
    """One run of a workflow for one subject (e.g. onboarding employee 003551 for SDP request 9001)."""

    __tablename__ = "core_job"

    workflow_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    module_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # draft | scheduled | running | waiting | failed | completed | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    subject_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    external_ref: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scheduled_for: Mapped[date | None] = mapped_column(Date)
    current_step: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    started_by: Mapped[str | None] = mapped_column(String(320))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Checklist version snapshotted at start; None = no step verification (older jobs, other workflows).
    checklist_version: Mapped[int | None] = mapped_column(Integer)

    steps: Mapped[list[JobStep]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobStep.seq", lazy="selectin"
    )
    evidence: Mapped[list[JobEvidence]] = relationship(
        cascade="all, delete-orphan", order_by="JobEvidence.created_at", lazy="selectin"
    )


class JobStep(UUIDPk, Base):
    __tablename__ = "core_job_step"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_job.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # auto | manual
    # pending | in_progress | waiting | verify | done | failed | skipped
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requires_confirm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(320))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str | None] = mapped_column(String(320))
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # [{"id", "text", "checked"}] snapshotted from the checklist version of the job.
    checklist: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    evidence_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_by: Mapped[str | None] = mapped_column(String(320))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verify_note: Mapped[str | None] = mapped_column(String(1000))

    job: Mapped[Job] = relationship(back_populates="steps")

    __table_args__ = (UniqueConstraint("job_id", "key", name="uq_core_job_step_job_key"),)


class JobEvent(Base):
    """Human-readable log of a job (shown in the UI); security-relevant actions also go to the audit log."""

    __tablename__ = "core_job_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_job.id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="info")  # info | warn | error
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(String(320))
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_core_job_event_job_at", "job_id", "at"),)


class JobEvidence(UUIDPk, Base):
    """Proof attached by an operator to a job step (screenshot / file, link or note)."""

    __tablename__ = "core_job_evidence"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_job.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # image | file | link | note
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core_file.id"))
    filename: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(1000))
    text: Mapped[str | None] = mapped_column(String(2000))
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkflowChecklist(UUIDPk, Base):
    """A version of the verification checklist of one workflow (edited by managers)."""

    __tablename__ = "core_workflow_checklist"
    __table_args__ = (UniqueConstraint("workflow_key", "version"),)

    workflow_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # {step_key: {"items": [{"id", "text"}], "evidence_required": bool}}
    steps: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
