"""playbook pinned to a Job

Revision ID: 0004_playbook
Revises: 0003_playbook
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_playbook"
down_revision: str | None = "0003_playbook"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playbook_job_run",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("guide_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("choices", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decisions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("pinned_by", sa.String(length=320), nullable=True),
        sa.ForeignKeyConstraint(
            ["guide_id"],
            ["playbook_guide.id"],
            name=op.f("fk_playbook_job_run_guide_id_playbook_guide"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["core_job.id"], name=op.f("fk_playbook_job_run_job_id_core_job"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_playbook_job_run")),
    )
    op.create_index(op.f("ix_playbook_job_run_guide_id"), "playbook_job_run", ["guide_id"])
    op.create_table(
        "playbook_job_mark",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("node_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("done_by", sa.String(length=320), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["playbook_job_run.job_id"],
            name=op.f("fk_playbook_job_mark_job_id_playbook_job_run"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", "node_id", name=op.f("pk_playbook_job_mark")),
    )


def downgrade() -> None:
    op.drop_table("playbook_job_mark")
    op.drop_index(op.f("ix_playbook_job_run_guide_id"), table_name="playbook_job_run")
    op.drop_table("playbook_job_run")
