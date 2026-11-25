from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk


class SyncRun(UUIDPk, Base):
    """One read of SDP + BioStar; snapshots and reconciliation rows hang off it."""

    __tablename__ = "lifecycle_sync_run"

    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # manual | scheduled
    requested_by: Mapped[str | None] = mapped_column(String(320))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running"
    )  # running|success|partial|failure
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    errors: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class LifecycleRequest(Base):
    """RTR request read from SDP, with fields parsed from its description. Current state, not per run."""

    __tablename__ = "lifecycle_request"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # SDP request id
    display_id: Mapped[str | None] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sdp_updated_ms: Mapped[str | None] = mapped_column(String(20))
    employee_id: Mapped[str | None] = mapped_column(String(32), index=True)
    full_name: Mapped[str | None] = mapped_column(String(256))
    change_type_raw: Mapped[str | None] = mapped_column(String(128))
    kind: Mapped[str | None] = mapped_column(String(32), index=True)
    effective_date: Mapped[date | None] = mapped_column(Date)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    parse_errors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    description_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # SDP technician of the request = person responsible for its onboard/offboard job.
    technician_id: Mapped[str | None] = mapped_column(String(32))
    technician_name: Mapped[str | None] = mapped_column(String(256))
    technician_email: Mapped[str | None] = mapped_column(String(320))
    # Band entered in infra-ops when the RTR request has none (or a wrong one); wins over the parsed value.
    band_override: Mapped[str | None] = mapped_column(String(32))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Snapshot(Base):
    """Normalized record of one system object as read in a given run (sdp_user, sdp_asset, biostar_user)."""

    __tablename__ = "lifecycle_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lifecycle_sync_run.id", ondelete="CASCADE"), nullable=False
    )
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    employee_id: Mapped[str | None] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_lifecycle_snapshot_run_system", "run_id", "system"),
        Index("ix_lifecycle_snapshot_employee", "employee_id"),
    )


class ReconRow(Base):
    """Reconciliation result for one employee in one run (kept for history)."""

    __tablename__ = "lifecycle_recon_row"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lifecycle_sync_run.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[str] = mapped_column(String(32), nullable=False)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    department: Mapped[str | None] = mapped_column(String(256))
    kind: Mapped[str | None] = mapped_column(String(32))
    max_severity: Mapped[str | None] = mapped_column(String(8))
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "employee_id", name="uq_lifecycle_recon_row_run_employee"),
        Index("ix_lifecycle_recon_row_employee", "employee_id"),
    )


class IssueAck(Base):
    """A discrepancy an operator has reviewed and marked as known (persists across runs)."""

    __tablename__ = "lifecycle_issue_ack"

    employee_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    rule: Mapped[str] = mapped_column(String(64), primary_key=True)
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    acked_by: Mapped[str] = mapped_column(String(320), nullable=False)
    acked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetReturn(Base):
    """An In Store asset that came back from someone (thu hồi), as opposed to new stock.

    SDP keeps only the current user, so the sync records it when an asset it saw assigned is back In Store
    (source "sync"); stock managers may also mark or unmark it by hand ("manual"). Cleared when the asset is
    assigned again.
    """

    __tablename__ = "lifecycle_asset_return"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    returned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    from_user: Mapped[str | None] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="sync")
    marked_by: Mapped[str] = mapped_column(String(320), nullable=False, default="system")
