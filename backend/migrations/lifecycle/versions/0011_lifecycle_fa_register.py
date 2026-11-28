"""stocktake accounting registers (sổ TSCĐ / CCDC) and the finance permission

Installations that already registered lifecycle.stocktake.finance give it to managers and supervisors here
(as 0010 did for assign); new installations get it at start-up.

Revision ID: 0011_lifecycle
Revises: 0010_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_lifecycle"
down_revision: str | None = "0010_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lifecycle_stocktake",
        sa.Column("fa_registers", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "lifecycle_fa_register_row",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "stocktake_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lifecycle_stocktake.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("row_no", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("code_norm", sa.String(128), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("group", sa.String(256), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4)),
        sa.Column("cost", sa.Numeric(18, 2)),
        sa.Column("accumulated", sa.Numeric(18, 2)),
        sa.Column("net", sa.Numeric(18, 2)),
        sa.Column("in_use_date", sa.Date()),
        sa.Column("department", sa.String(256), nullable=False),
        sa.Column("life_months", sa.Integer()),
        sa.Column("account", sa.String(32), nullable=False),
        sa.Column("serial", sa.String(128), nullable=False),
    )
    op.create_index(
        "ix_lifecycle_fa_row_code", "lifecycle_fa_register_row", ["stocktake_id", "kind", "code_norm"]
    )
    op.execute(
        "INSERT INTO core_role_permission (role_id, permission_code) "
        "SELECT r.id, 'lifecycle.stocktake.finance' FROM core_role r "
        "WHERE r.code IN ('manager', 'supervisor') "
        "AND EXISTS (SELECT 1 FROM core_permission WHERE code = 'lifecycle.stocktake.finance') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    # Note: this removes the grant even when an admin gave it to other roles by hand.
    op.execute("DELETE FROM core_role_permission WHERE permission_code = 'lifecycle.stocktake.finance'")
    op.drop_index("ix_lifecycle_fa_row_code", table_name="lifecycle_fa_register_row")
    op.drop_table("lifecycle_fa_register_row")
    op.drop_column("lifecycle_stocktake", "fa_registers")
