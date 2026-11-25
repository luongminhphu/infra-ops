"""personal credentials (a person's own API key per connection)

Revision ID: 0009_core
Revises: 0008_core
Create Date: 2026-11-25 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_core"
down_revision: str | None = "0008_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "core_user_credential",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("auth_type", sa.String(length=48), nullable=False),
        sa.Column("public", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(length=16), nullable=False),
        sa.Column("hint", sa.String(length=16), nullable=False),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_user.id"],
            name=op.f("fk_core_user_credential_user_id_core_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["core_connection_profile.id"],
            name=op.f("fk_core_user_credential_profile_id_core_connection_profile"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_core_user_credential")),
        sa.UniqueConstraint("user_id", "profile_id", name="uq_core_user_credential_user_profile"),
    )
    op.create_index(
        op.f("ix_core_user_credential_user_id"), "core_user_credential", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_core_user_credential_user_id"), table_name="core_user_credential")
    op.drop_table("core_user_credential")
