"""lifecycle.sync runs every 4 hours (was 30 minutes)

Scheduled-task rows keep their stored interval, so the new manifest default only reaches new installs: move rows
still on the old 30-minute default.

Revision ID: 0008_lifecycle
Revises: 0007_lifecycle
Create Date: 2026-11-27 17:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_lifecycle"
down_revision: str | None = "0007_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "0002_core"


def upgrade() -> None:
    op.execute(
        "UPDATE core_scheduled_task SET interval_seconds = 14400 "
        "WHERE key = 'lifecycle.sync' AND interval_seconds = 1800"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE core_scheduled_task SET interval_seconds = 1800 "
        "WHERE key = 'lifecycle.sync' AND interval_seconds = 14400"
    )
