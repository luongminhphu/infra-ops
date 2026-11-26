"""stocktake areas (khu kiểm kê) and per-stocktake zones with an assignee

Revision ID: 0006_lifecycle
Revises: 0005_lifecycle
Create Date: 2026-11-26 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_lifecycle"
down_revision: str | None = "0005_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lifecycle_stocktake_area",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_store", sa.Boolean(), nullable=False),
        sa.Column("departments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("employee_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=320), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_stocktake_area")),
        sa.UniqueConstraint("name", name=op.f("uq_lifecycle_stocktake_area_name")),
    )
    op.create_table(
        "lifecycle_stocktake_zone",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stocktake_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_store", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignee_name", sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(
            ["stocktake_id"],
            ["lifecycle_stocktake.id"],
            name=op.f("fk_lifecycle_stocktake_zone_stocktake_id_lifecycle_stocktake"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_stocktake_zone")),
    )
    op.create_index(
        op.f("ix_lifecycle_stocktake_zone_stocktake_id"), "lifecycle_stocktake_zone", ["stocktake_id"]
    )
    op.add_column("lifecycle_stocktake_item", sa.Column("employee_id", sa.String(length=32), nullable=True))
    op.add_column("lifecycle_stocktake_item", sa.Column("department", sa.String(length=256), nullable=True))
    op.add_column(
        "lifecycle_stocktake_item", sa.Column("zone_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_lifecycle_stocktake_item_zone_id_lifecycle_stocktake_zone"),
        "lifecycle_stocktake_item",
        "lifecycle_stocktake_zone",
        ["zone_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_lifecycle_stocktake_item_zone_id_lifecycle_stocktake_zone"),
        "lifecycle_stocktake_item",
        type_="foreignkey",
    )
    op.drop_column("lifecycle_stocktake_item", "zone_id")
    op.drop_column("lifecycle_stocktake_item", "department")
    op.drop_column("lifecycle_stocktake_item", "employee_id")
    op.drop_index(op.f("ix_lifecycle_stocktake_zone_stocktake_id"), table_name="lifecycle_stocktake_zone")
    op.drop_table("lifecycle_stocktake_zone")
    op.drop_table("lifecycle_stocktake_area")
