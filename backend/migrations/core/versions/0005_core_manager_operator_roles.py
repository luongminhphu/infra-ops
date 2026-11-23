"""manager / operator roles (helpdesk renamed to operator)

Revision ID: 0005_core
Revises: 0004_core
Create Date: 2026-11-23 10:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_core"
down_revision: str | None = "0004_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE core_role SET code = 'operator', name = 'Operator', "
        "description = 'Thực hiện các task trên app và xem dữ liệu vận hành.' WHERE code = 'helpdesk'"
    )
    # On a fresh database there are no roles yet: startup creates them and grants defaults.
    op.execute(
        "INSERT INTO core_role (id, code, name, description, is_system, all_permissions) "
        "SELECT gen_random_uuid(), 'manager', 'Manager', "
        "'Giao và điều chuyển task giữa các Operator, xem thống kê.', true, false "
        "WHERE EXISTS (SELECT 1 FROM core_role WHERE code = 'operator') "
        "AND NOT EXISTS (SELECT 1 FROM core_role WHERE code = 'manager')"
    )
    op.execute(
        "INSERT INTO core_role_permission (role_id, permission_code) "
        "SELECT m.id, x.code FROM core_role m, ("
        "  SELECT rp.permission_code AS code FROM core_role_permission rp "
        "  JOIN core_role o ON o.id = rp.role_id WHERE o.code = 'operator' "
        # Extra permissions a manager gets on top of everything the operator role already has.
        "  UNION SELECT code FROM core_permission "
        "  WHERE code IN ('lifecycle.job.override_close', 'core.audit.view')"
        ") x WHERE m.code = 'manager' ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM core_role WHERE code = 'manager'")
    op.execute("UPDATE core_role SET code = 'helpdesk', name = 'Helpdesk' WHERE code = 'operator'")
