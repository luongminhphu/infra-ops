"""year-end IT asset stocktake (kiểm kê)

Revision ID: 0005_lifecycle
Revises: 0004_lifecycle
Create Date: 2026-11-26 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_lifecycle"
down_revision: str | None = "0004_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lifecycle_stocktake",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("cutoff", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_by", sa.String(length=320), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_stocktake")),
    )
    op.create_table(
        "lifecycle_stocktake_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("stocktake_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("product_type", sa.String(length=128), nullable=True),
        sa.Column("asset_tag", sa.String(length=128), nullable=True),
        sa.Column("serial", sa.String(length=128), nullable=True),
        sa.Column("fa_code", sa.String(length=128), nullable=True),
        sa.Column("state", sa.String(length=64), nullable=True),
        sa.Column("user_name", sa.String(length=256), nullable=True),
        sa.Column("user_email", sa.String(length=320), nullable=True),
        sa.Column("site", sa.String(length=128), nullable=True),
        sa.Column("count", sa.String(length=16), nullable=False),
        sa.Column("condition", sa.String(length=16), nullable=True),
        sa.Column("seen_at", sa.String(length=256), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("counted_by", sa.String(length=320), nullable=True),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("resolution_note", sa.String(length=1000), nullable=False),
        sa.Column("resolved_by", sa.String(length=320), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["stocktake_id"],
            ["lifecycle_stocktake.id"],
            name=op.f("fk_lifecycle_stocktake_item_stocktake_id_lifecycle_stocktake"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_stocktake_item")),
        sa.UniqueConstraint("stocktake_id", "asset_id", name="uq_lifecycle_stocktake_item_asset"),
    )
    op.create_index("ix_lifecycle_stocktake_item_stocktake", "lifecycle_stocktake_item", ["stocktake_id"])


def downgrade() -> None:
    op.drop_index("ix_lifecycle_stocktake_item_stocktake", table_name="lifecycle_stocktake_item")
    op.drop_table("lifecycle_stocktake_item")
    op.drop_table("lifecycle_stocktake")
