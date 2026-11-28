"""core_document.job_id: link records to the Job that created them (row scope)

Revision ID: 0011_core
Revises: 0010_core
Create Date: 2026-11-28 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0011_core"
down_revision: str | None = "0010_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Backfill from step outputs {"document_id": "<uuid>"}; non-UUID values are ignored.
BACKFILL_SQL = """
UPDATE core_document d SET job_id = s.job_id
FROM core_job_step s
WHERE jsonb_typeof(s.output) = 'object'
  AND d.id = CASE WHEN s.output ->> 'document_id'
        ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
      THEN (s.output ->> 'document_id')::uuid END
"""


def upgrade() -> None:
    op.add_column(
        "core_document",
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("core_job.id", ondelete="SET NULL", name="fk_core_document_job_id_core_job"),
        ),
    )
    op.create_index("ix_core_document_job_id", "core_document", ["job_id"])
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_index("ix_core_document_job_id", table_name="core_document")
    op.drop_column("core_document", "job_id")
