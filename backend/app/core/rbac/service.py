"""Role/permission resolution and startup synchronisation of module permissions."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.registry import ModuleRegistry
from app.core.rbac.models import KnownPermission, Role, RolePermission, UserRole

# Ordered from least to most privileged (the order roles are listed in the UI).
SYSTEM_ROLES: dict[str, tuple[str, str, bool]] = {
    "viewer": ("Viewer", "Xem dữ liệu, không thay đổi.", False),
    "operator": ("Operator", "Thực hiện các task trên app và xem dữ liệu vận hành.", False),
    "supervisor": (
        "Supervisor",
        "Xem và điều phối task của các Operator, làm việc Helpdesk/Support; không cấu hình hệ thống, không quản lý người dùng.",
        False,
    ),
    "manager": (
        "Manager",
        "Toàn bộ task của Operator, điều phối, cấu hình module và đóng Job bắt buộc; không quản lý kết nối và người dùng.",
        False,
    ),
    "admin": ("Administrator", "Toàn quyền trên mọi module, không chỉnh sửa được.", True),
}
# Earlier default names, renamed on startup unless an admin already changed them.
_OLD_NAMES = {"admin": "Quản trị", "viewer": "Chỉ xem"}


def role_rank(code: str) -> int:
    """Position of a system role from least to most privileged; custom roles come after."""
    order = list(SYSTEM_ROLES)
    return order.index(code) if code in order else len(order)


async def sync_permissions(session: AsyncSession, registry: ModuleRegistry) -> list[str]:
    """Create system roles and grant default roles for permissions seen for the first time.

    Returns the list of newly registered permission codes. Idempotent.
    """
    roles = {r.code: r for r in (await session.scalars(select(Role))).all()}
    for code, (name, description, all_perms) in SYSTEM_ROLES.items():
        if code not in roles:
            role = Role(
                code=code, name=name, description=description, is_system=True, all_permissions=all_perms
            )
            session.add(role)
            roles[code] = role
        elif roles[code].name == _OLD_NAMES.get(code):
            roles[code].name = name
    await session.flush()

    known = set((await session.scalars(select(KnownPermission.code))).all())
    new_codes: list[str] = []
    for module_id, perm in registry.permissions():
        if perm.code in known:
            continue
        session.add(KnownPermission(code=perm.code, module_id=module_id))
        for role_code in perm.default_roles:
            role = roles.get(role_code)
            if role is not None and not role.all_permissions:
                session.add(RolePermission(role_id=role.id, permission_code=perm.code))
        new_codes.append(perm.code)
    await session.flush()
    return new_codes


async def permissions_for_user(
    session: AsyncSession, user_id: uuid.UUID, registry: ModuleRegistry
) -> set[str]:
    """Effective permissions, limited to permissions of enabled modules."""
    roles = (
        await session.scalars(
            select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
        )
    ).all()
    if any(r.all_permissions for r in roles):
        return {code for _, p in registry.permissions() for code in [p.code]}
    if not roles:
        return set()
    codes = (
        await session.scalars(
            select(RolePermission.permission_code).where(RolePermission.role_id.in_([r.id for r in roles]))
        )
    ).all()
    return {c for c in codes if registry.has_permission(c)}


async def role_codes_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    return list(
        (
            await session.scalars(
                select(Role.code)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user_id)
            )
        ).all()
    )


async def set_user_roles(session: AsyncSession, user_id: uuid.UUID, role_ids: list[uuid.UUID]) -> None:
    await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
    for rid in dict.fromkeys(role_ids):
        session.add(UserRole(user_id=user_id, role_id=rid))
    await session.flush()


async def grant_role(session: AsyncSession, user_id: uuid.UUID, role_code: str) -> None:
    role = await session.scalar(select(Role).where(Role.code == role_code))
    if role is None:
        raise ValueError(f"Unknown role {role_code}")
    exists = await session.get(UserRole, (user_id, role.id))
    if exists is None:
        session.add(UserRole(user_id=user_id, role_id=role.id))
        await session.flush()


async def count_active_admins(session: AsyncSession) -> int:
    from app.core.auth.models import User

    rows = (
        await session.scalars(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .join(User, User.id == UserRole.user_id)
            .where(Role.all_permissions.is_(True), User.is_active.is_(True))
        )
    ).all()
    return len(set(rows))
