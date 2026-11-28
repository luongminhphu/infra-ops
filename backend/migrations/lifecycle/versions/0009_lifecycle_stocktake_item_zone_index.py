"""index lifecycle_stocktake_item.zone_id

Revision ID: 0009_lifecycle
Revises: 0008_lifecycle
Create Date: 2026-11-27 18:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_lifecycle"
down_revision: str | None = "0008_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_lifecycle_stocktake_item_zone_id", "lifecycle_stocktake_item", ["zone_id"])


def downgrade() -> None:
    op.drop_index("ix_lifecycle_stocktake_item_zone_id", table_name="lifecycle_stocktake_item")
