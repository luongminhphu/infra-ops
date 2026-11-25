"""returned (thu hồi) stock assets

Revision ID: 0004_lifecycle
Revises: 0003_lifecycle
Create Date: 2026-11-25 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_lifecycle"
down_revision: str | None = "0003_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lifecycle_asset_return",
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("returned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("from_user", sa.String(length=256), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("marked_by", sa.String(length=320), nullable=False),
        sa.PrimaryKeyConstraint("asset_id", name=op.f("pk_lifecycle_asset_return")),
    )


def downgrade() -> None:
    op.drop_table("lifecycle_asset_return")
