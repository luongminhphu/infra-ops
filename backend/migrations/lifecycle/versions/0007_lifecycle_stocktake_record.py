"""stocktake record (biên bản kiểm kê) document link

Revision ID: 0007_lifecycle
Revises: 0006_lifecycle
Create Date: 2026-11-26 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_lifecycle"
down_revision: str | None = "0006_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lifecycle_stocktake", sa.Column("record_document_id", postgresql.UUID(as_uuid=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("lifecycle_stocktake", "record_document_id")
