"""workload ticket category

Revision ID: 0007_workload
Revises: 0006_workload
Create Date: 2026-11-28 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_workload"
down_revision: str | None = "0006_workload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workload_ticket", sa.Column("category", sa.String(length=256), nullable=True))
    op.add_column("workload_ticket", sa.Column("subcategory", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("workload_ticket", "subcategory")
    op.drop_column("workload_ticket", "category")
