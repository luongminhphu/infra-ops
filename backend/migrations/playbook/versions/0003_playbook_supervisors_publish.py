"""supervisors publish playbooks

Managers and supervisors now design playbooks end to end (add, change, replace, delete). Installations that
already registered the permission get the new default grant here; new installations get it at start-up.

Revision ID: 0003_playbook
Revises: 0002_playbook
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_playbook"
down_revision: str | None = "0002_playbook"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO core_role_permission (role_id, permission_code) "
        "SELECT r.id, 'playbook.guides.publish' FROM core_role r "
        "WHERE r.code = 'supervisor' AND EXISTS (SELECT 1 FROM core_permission WHERE code = 'playbook.guides.publish') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM core_role_permission WHERE permission_code = 'playbook.guides.publish' "
        "AND role_id IN (SELECT id FROM core_role WHERE code = 'supervisor')"
    )
