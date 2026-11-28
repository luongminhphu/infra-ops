"""stocktake simplification: rules_version, suggested resolutions, supervisors assign zones

Stocktakes closed before this keep rules_version 1 (light damage was a difference). Open ones move to 2:
resolutions orphaned by "damaged is no longer a difference" are cleared. Installations that already registered
lifecycle.stocktake.assign give it to supervisors here; new installations get it at start-up.

Revision ID: 0010_lifecycle
Revises: 0009_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_lifecycle"
down_revision: str | None = "0009_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lifecycle_stocktake", sa.Column("rules_version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "lifecycle_stocktake_item",
        sa.Column("resolution_suggested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE lifecycle_stocktake_item SET resolution = NULL, resolution_note = '', resolved_by = NULL, "
        "resolved_at = NULL WHERE condition = 'damaged' AND count = 'found' AND stocktake_id IN "
        "(SELECT id FROM lifecycle_stocktake WHERE status <> 'closed')"
    )
    op.execute("UPDATE lifecycle_stocktake SET rules_version = 2 WHERE status <> 'closed'")
    op.alter_column("lifecycle_stocktake", "rules_version", server_default="2")  # new stocktakes
    op.execute(
        "INSERT INTO core_role_permission (role_id, permission_code) "
        "SELECT r.id, 'lifecycle.stocktake.assign' FROM core_role r "
        "WHERE r.code = 'supervisor' "
        "AND EXISTS (SELECT 1 FROM core_permission WHERE code = 'lifecycle.stocktake.assign') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    # Note: this removes the supervisor grant of lifecycle.stocktake.assign even when an admin gave it by hand.
    op.execute(
        "DELETE FROM core_role_permission WHERE permission_code = 'lifecycle.stocktake.assign' "
        "AND role_id IN (SELECT id FROM core_role WHERE code = 'supervisor')"
    )
    op.drop_column("lifecycle_stocktake_item", "resolution_suggested")
    op.drop_column("lifecycle_stocktake", "rules_version")
