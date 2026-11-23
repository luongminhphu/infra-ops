"""request technician (job assignee)

Revision ID: 0002_lifecycle
Revises: 0001_lifecycle
Create Date: 2026-11-23 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_lifecycle"
down_revision: str | None = "0001_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lifecycle_request", sa.Column("technician_id", sa.String(length=32), nullable=True))
    op.add_column("lifecycle_request", sa.Column("technician_name", sa.String(length=256), nullable=True))
    op.add_column("lifecycle_request", sa.Column("technician_email", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("lifecycle_request", "technician_email")
    op.drop_column("lifecycle_request", "technician_name")
    op.drop_column("lifecycle_request", "technician_id")
