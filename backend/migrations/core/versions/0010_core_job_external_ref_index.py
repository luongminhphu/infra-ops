"""index core_job.external_ref

Revision ID: 0010_core
Revises: 0009_core
Create Date: 2026-11-27 18:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_core"
down_revision: str | None = "0009_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_core_job_external_ref", "core_job", ["external_ref"])


def downgrade() -> None:
    op.drop_index("ix_core_job_external_ref", table_name="core_job")
