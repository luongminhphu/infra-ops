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


class Stocktake(UUIDPk, Base):
    """A year-end IT asset count (kiểm kê): the SDP book closed at creation, then counted on site.

    counting → reviewing (count finished: what was not seen is missing) → closed (every difference handled).
    """

    __tablename__ = "lifecycle_stocktake"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cutoff: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="counting")
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_by: Mapped[str | None] = mapped_column(String(320))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Biên bản kiểm kê (core document) signed by IT and Kế toán.
    record_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class StocktakeArea(UUIDPk, Base):
    """A stocktake area (khu kiểm kê, usually a floor), kept from year to year.

    Its people are the SDP users of the listed departments plus the listed employee IDs (the IDs win when a
    department is split across floors). The store area takes the assets nobody uses (Kho IT).
    """

    __tablename__ = "lifecycle_stocktake_area"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_store: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    departments: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    employee_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StocktakeZone(UUIDPk, Base):
    """An area as it stood when a stocktake was opened, and who counts it this time."""

    __tablename__ = "lifecycle_stocktake_zone"

    stocktake_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lifecycle_stocktake.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_store: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assignee_name: Mapped[str | None] = mapped_column(String(256))


class StocktakeItem(Base):
    """One asset of a stocktake: from the SDP book (asset_id set) or found on site but not in SDP (extra)."""

    __tablename__ = "lifecycle_stocktake_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stocktake_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lifecycle_stocktake.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    product_type: Mapped[str | None] = mapped_column(String(128))
    asset_tag: Mapped[str | None] = mapped_column(String(128))
    serial: Mapped[str | None] = mapped_column(String(128))
    fa_code: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str | None] = mapped_column(String(64))
    user_name: Mapped[str | None] = mapped_column(String(256))
    user_email: Mapped[str | None] = mapped_column(String(320))
    site: Mapped[str | None] = mapped_column(String(128))
    employee_id: Mapped[str | None] = mapped_column(String(32))
    department: Mapped[str | None] = mapped_column(String(256))
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lifecycle_stocktake_zone.id", ondelete="SET NULL")
    )
    count: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # pending|found|missing|extra
    condition: Mapped[str | None] = mapped_column(String(16))  # good|damaged|broken
    seen_at: Mapped[str] = mapped_column(String(256), nullable=False, default="")  # where it was seen
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    counted_by: Mapped[str | None] = mapped_column(String(320))
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(32))
    resolution_note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    resolved_by: Mapped[str | None] = mapped_column(String(320))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("stocktake_id", "asset_id", name="uq_lifecycle_stocktake_item_asset"),
        Index("ix_lifecycle_stocktake_item_stocktake", "stocktake_id"),
    )


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
