"""scheduled digest of next actions: per-user opt-in, sent log, last-sent priority per item

Revision ID: 0012_core
Revises: 0011_core
Create Date: 2026-11-28 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_core"
down_revision: str | None = "0011_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "core_notification_pref",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("email_digest", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_user.id"],
            name=op.f("fk_core_notification_pref_user_id_core_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_core_notification_pref")),
    )
    op.create_table(
        "core_notification_sent",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("item_key", sa.String(length=256), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_user.id"],
            name=op.f("fk_core_notification_sent_user_id_core_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_core_notification_sent")),
        sa.UniqueConstraint("user_id", "channel", "slot", name="uq_core_notification_sent_user_channel_slot"),
    )
    op.create_index(
        "uq_core_notification_sent_team_slot",
        "core_notification_sent",
        ["channel", "slot"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_table(
        "core_notification_item",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("item_key", sa.String(length=256), nullable=False),
        sa.Column("last_priority", sa.Integer(), nullable=False),
        sa.Column("last_title", sa.String(length=512), server_default="", nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_user.id"],
            name=op.f("fk_core_notification_item_user_id_core_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "item_key", name=op.f("pk_core_notification_item")),
    )


def downgrade() -> None:
    op.drop_table("core_notification_item")
    op.drop_index("uq_core_notification_sent_team_slot", table_name="core_notification_sent")
    op.drop_table("core_notification_sent")
    op.drop_table("core_notification_pref")
