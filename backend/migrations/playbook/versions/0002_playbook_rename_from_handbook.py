"""rename handbook to playbook

Tables, keys and indexes move from handbook_* to playbook_*; image URLs inside the stored maps and the
roles' permission grants and the known-permission registry follow the new name, so nothing already written or granted is lost.

Revision ID: 0002_playbook
Revises: 0001_handbook
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_playbook"
down_revision: str | None = "0001_handbook"
branch_labels: str | Sequence[str] | None = ("playbook",)
depends_on: str | Sequence[str] | None = None

TABLES = ["guide", "guide_version", "image"]
CONSTRAINTS = [  # (table suffix, constraint name with {p} for the prefix)
    ("guide", "pk_{p}_guide"),
    ("guide_version", "pk_{p}_guide_version"),
    ("guide_version", "fk_{p}_guide_version_guide_id_{p}_guide"),
    ("guide_version", "uq_{p}_guide_version"),
    ("image", "pk_{p}_image"),
    ("image", "fk_{p}_image_file_id_core_file"),
    ("image", "fk_{p}_image_guide_id_{p}_guide"),
]
INDEXES = ["ix_{p}_guide_version_guide_id", "ix_{p}_image_guide_id"]


def _move(a: str, b: str) -> None:
    """Rename everything from prefix ``a`` to prefix ``b``."""
    for t in TABLES:
        op.execute(f"ALTER TABLE {a}_{t} RENAME TO {b}_{t}")
    op.execute(f"ALTER SEQUENCE IF EXISTS {a}_guide_version_id_seq RENAME TO {b}_guide_version_id_seq")
    for t, name in CONSTRAINTS:
        op.execute(f"ALTER TABLE {b}_{t} RENAME CONSTRAINT {name.format(p=a)} TO {name.format(p=b)}")
    for name in INDEXES:
        op.execute(f"ALTER INDEX {name.format(p=a)} RENAME TO {name.format(p=b)}")
    # Identifiers are the constants above (no user input).
    for table, col in ((f"{b}_guide", "draft"), (f"{b}_guide_version", "tree")):
        sql = (
            f"UPDATE {table} SET {col} = replace({col}::text, '/api/{a}/images/', '/api/{b}/images/')::jsonb"  # noqa: S608
        )
        op.execute(sql)
    sql = f"UPDATE core_permission SET code = replace(code, '{a}.', '{b}.'), module_id = '{b}' WHERE code LIKE '{a}.%'"  # noqa: S608
    op.execute(sql)
    sql = f"UPDATE core_role_permission SET permission_code = replace(permission_code, '{a}.', '{b}.') WHERE permission_code LIKE '{a}.%'"  # noqa: S608
    op.execute(sql)


def upgrade() -> None:
    _move("handbook", "playbook")


def downgrade() -> None:
    _move("playbook", "handbook")
