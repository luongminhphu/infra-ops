"""supervisor role; manager gets module settings

Revision ID: 0006_core
Revises: 0005_core
Create Date: 2026-11-23 14:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_core"
down_revision: str | None = "0005_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only for databases that already have roles; a fresh database gets them at startup.
    op.execute(
        "INSERT INTO core_role (id, code, name, description, is_system, all_permissions) "
        "SELECT gen_random_uuid(), 'supervisor', 'Supervisor', "
        "'Xem và điều phối task của các Operator, làm việc Helpdesk/Support; "
        "không cấu hình hệ thống, không quản lý người dùng.', true, false "
        "WHERE EXISTS (SELECT 1 FROM core_role WHERE code = 'operator') "
        "AND NOT EXISTS (SELECT 1 FROM core_role WHERE code = 'supervisor')"
    )
    # Supervisor = operator's current grants + coordination. Only permissions the platform already
    # knows are inserted: unknown ones are granted by their manifest defaults at startup.
    op.execute(
        "INSERT INTO core_role_permission (role_id, permission_code) "
        "SELECT s.id, x.code FROM core_role s, ("
        "  SELECT rp.permission_code AS code FROM core_role_permission rp "
        "  JOIN core_role o ON o.id = rp.role_id WHERE o.code = 'operator' "
        "  UNION SELECT code FROM core_permission "
        "  WHERE code IN ('workload.tickets.assign', 'workload.tickets.view_all')"
        ") x WHERE s.code = 'supervisor' ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO core_role_permission (role_id, permission_code) "
        "SELECT m.id, p.code FROM core_role m, core_permission p "
        "WHERE m.code = 'manager' AND p.code IN ('lifecycle.settings.manage', 'lifecycle.templates.manage') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM core_role WHERE code = 'supervisor'")
