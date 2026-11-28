from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPk


class TemplateVersion(UUIDPk, Timestamps, Base):
    """A DOCX template version for a document kind. Versions are never edited or deleted."""

    __tablename__ = "core_template_version"

    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core_file.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    placeholders: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    unknown_placeholders: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    uploaded_by: Mapped[str | None] = mapped_column(String(320))

    __table_args__ = (UniqueConstraint("kind", "version", name="uq_core_template_version_kind_version"),)


class Document(UUIDPk, Timestamps, Base):
    """A generated record (e.g. handover). Data is frozen once finalized; the number is allocated then."""

    __tablename__ = "core_document"

    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    module_id: Mapped[str] = mapped_column(String(64), nullable=False)
    number: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft"
    )  # draft|finalized|signed|void
    subject_key: Mapped[str | None] = mapped_column(String(64), index=True)  # e.g. employee ID
    subject_label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    external_ref: Mapped[str | None] = mapped_column(String(64))  # e.g. SDP request id
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_template_version.id")
    )
    docx_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core_file.id"))
    pdf_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core_file.id"))
    signed_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core_file.id"))
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_job.id", ondelete="SET NULL"), index=True
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[str | None] = mapped_column(String(320))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_by: Mapped[str | None] = mapped_column(String(320))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(500))
    # Results of side actions (e.g. attached to SDP request) – informational.
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DocumentSequence(Base):
    """Monthly counters for document numbers: PREFIX-YYYYMM-####."""

    __tablename__ = "core_document_sequence"

    prefix: Mapped[str] = mapped_column(String(16), primary_key=True)
    period: Mapped[str] = mapped_column(String(6), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
