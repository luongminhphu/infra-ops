"""Users, roles and permission matrix (admin only)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.auth.models import User
from app.core.auth.passwords import hash_password, password_problems
from app.core.auth.service import find_user_by_upn
from app.core.auth.sessions import destroy_user_sessions
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AppError, Conflict, NotFound
from app.core.modules.registry import get_registry
from app.core.permissions import RBAC_MANAGE
from app.core.rbac.models import Role, RolePermission, UserRole
from app.core.rbac.service import count_active_admins, set_user_roles

router = APIRouter(tags=["access"])
manage = require(RBAC_MANAGE)


class PermissionOut(BaseModel):
    code: str
    description: str


class PermissionGroupOut(BaseModel):
    module: str
    label: str
    permissions: list[PermissionOut]


@router.get("/permissions", response_model=list[PermissionGroupOut])
async def list_permissions(_: CurrentUser = Depends(manage)) -> list[PermissionGroupOut]:
    reg = get_registry()
    return [
        PermissionGroupOut(
            module=m.id,
            label=m.name,
            permissions=[PermissionOut(code=p.code, description=p.description) for p in m.permissions],
        )
        for m in reg.modules
        if m.permissions
    ]


class RoleOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str
    is_system: bool
    all_permissions: bool
    permissions: list[str]
    user_count: int


async def _role_out(session: AsyncSession, role: Role) -> RoleOut:
    perms = (
        await session.scalars(select(RolePermission.permission_code).where(RolePermission.role_id == role.id))
    ).all()
    count = await session.scalar(
        select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id)
    )
    reg = get_registry()
    return RoleOut(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        all_permissions=role.all_permissions,
        permissions=sorted(p.code for _, p in reg.permissions())
        if role.all_permissions
        else sorted(c for c in perms if reg.has_permission(c)),
        user_count=count or 0,
    )


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    _: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> list[RoleOut]:
    roles = (await session.scalars(select(Role).order_by(Role.is_system.desc(), Role.name))).all()
    return [await _role_out(session, r) for r in roles]


class RoleIn(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    permissions: list[str] = Field(default_factory=list)


class RolePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    permissions: list[str] | None = None


def _validate_perms(codes: list[str]) -> list[str]:
    reg = get_registry()
    unknown = [c for c in codes if not reg.has_permission(c)]
    if unknown:
        raise AppError("Quyền không tồn tại.", code="unknown_permission", details={"unknown": unknown})
    return sorted(set(codes))


async def _set_role_perms(session: AsyncSession, role: Role, codes: list[str]) -> None:
    existing = (await session.scalars(select(RolePermission).where(RolePermission.role_id == role.id))).all()
    reg = get_registry()
    for row in existing:
        # Keep grants of disabled modules untouched; only replace codes the UI can see.
        if reg.has_permission(row.permission_code):
            await session.delete(row)
    await session.flush()
    for code in codes:
        session.add(RolePermission(role_id=role.id, permission_code=code))
    await session.flush()


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(
    body: RoleIn, user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> RoleOut:
    if await session.scalar(select(Role).where(Role.code == body.code)):
        raise Conflict("Mã vai trò đã tồn tại.")
    perms = _validate_perms(body.permissions)
    role = Role(code=body.code, name=body.name, description=body.description)
    session.add(role)
    await session.flush()
    await _set_role_perms(session, role, perms)
    await record(
        session,
        actor=user.actor,
        module_id="core",
        action="rbac.role_created",
        target_type="role",
        target_id=str(role.id),
        target_label=role.name,
        after={"code": role.code, "permissions": perms},
    )
    out = await _role_out(session, role)
    await session.commit()
    return out


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: uuid.UUID,
    body: RolePatch,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> RoleOut:
    role = await session.get(Role, role_id)
    if role is None:
        raise NotFound("Không tìm thấy vai trò.")
    before = await _role_out(session, role)
    if role.all_permissions and body.permissions is not None:
        raise AppError("Vai trò Quản trị luôn có toàn quyền, không chỉnh được.", code="admin_role_locked")
    if body.name is not None:
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.permissions is not None:
        await _set_role_perms(session, role, _validate_perms(body.permissions))
    after = await _role_out(session, role)
    b = before.model_dump(include={"name", "description", "permissions"})
    a = after.model_dump(include={"name", "description", "permissions"})
    await record(
        session,
        actor=user.actor,
        module_id="core",
        action="rbac.role_updated",
        target_type="role",
        target_id=str(role.id),
        target_label=role.name,
        before=b,
        after=a,
    )
    await session.commit()
    return after


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: uuid.UUID, user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> None:
    role = await session.get(Role, role_id)
    if role is None:
        raise NotFound("Không tìm thấy vai trò.")
    if role.is_system:
        raise AppError("Không xoá được vai trò hệ thống.", code="system_role")
    await record(
        session,
        actor=user.actor,
        module_id="core",
        action="rbac.role_deleted",
        target_type="role",
        target_id=str(role.id),
        target_label=role.name,
    )
    await session.delete(role)
    await session.commit()


class UserOut(BaseModel):
    id: uuid.UUID
    upn: str
    display_name: str
    email: str | None
    is_active: bool
    is_local: bool
    locked: bool
    last_login_at: datetime | None
    role_ids: list[uuid.UUID]


async def _user_out(session: AsyncSession, u: User) -> UserOut:
    from datetime import UTC
    from datetime import datetime as dt

    role_ids = (await session.scalars(select(UserRole.role_id).where(UserRole.user_id == u.id))).all()
    return UserOut(
        id=u.id,
        upn=u.upn,
        display_name=u.display_name,
        email=u.email,
        is_active=u.is_active,
        is_local=u.password_hash is not None,
        locked=bool(u.locked_until and u.locked_until > dt.now(UTC)),
        last_login_at=u.last_login_at,
        role_ids=list(role_ids),
    )


@router.get("/users", response_model=list[UserOut])
async def list_users(
    _: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> list[UserOut]:
    users = (await session.scalars(select(User).order_by(User.display_name))).all()
    return [await _user_out(session, u) for u in users]


class UserIn(BaseModel):
    upn: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=256)
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    # Required in local auth mode; ignored for SSO.
    password: str | None = Field(default=None, max_length=256)


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    is_active: bool | None = None
    role_ids: list[uuid.UUID] | None = None
    password: str | None = Field(default=None, max_length=256)
    unlock: bool = False


async def _check_roles(session: AsyncSession, role_ids: list[uuid.UUID]) -> None:
    if role_ids:
        found = (await session.scalars(select(Role.id).where(Role.id.in_(role_ids)))).all()
        if len(set(found)) != len(set(role_ids)):
            raise AppError("Vai trò không tồn tại.", code="unknown_role")


def _check_password(password: str) -> str:
    problems = password_problems(password, get_settings().local_min_password_length)
    if problems:
        raise AppError(" ".join(problems), code="weak_password")
    return hash_password(password)


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserIn, user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> UserOut:
    if await find_user_by_upn(session, body.upn):
        raise Conflict("Người dùng đã tồn tại.")
    await _check_roles(session, body.role_ids)
    new = User(upn=body.upn.strip().lower(), display_name=body.display_name)
    if get_settings().auth_mode == "local":
        if not body.password:
            raise AppError("Cần đặt mật khẩu ban đầu cho tài khoản.", code="password_required")
        new.password_hash = _check_password(body.password)
    session.add(new)
    await session.flush()
    await set_user_roles(session, new.id, body.role_ids)
    await record(
        session,
        actor=user.actor,
        module_id="core",
        action="rbac.user_created",
        target_type="user",
        target_id=str(new.id),
        target_label=new.upn,
        after={"display_name": new.display_name, "role_ids": [str(r) for r in body.role_ids]},
    )
    out = await _user_out(session, new)
    await session.commit()
    return out


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserPatch,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    target = await session.get(User, user_id)
    if target is None:
        raise NotFound("Không tìm thấy người dùng.")
    before = (await _user_out(session, target)).model_dump(
        mode="json", include={"display_name", "is_active", "role_ids"}
    )
    if body.is_active is False and target.id == user.id:
        raise AppError("Không thể tự vô hiệu hoá tài khoản của chính mình.", code="self_deactivate")
    if body.display_name is not None:
        target.display_name = body.display_name
    if body.is_active is not None:
        target.is_active = body.is_active
        if not body.is_active:
            await destroy_user_sessions(session, target.id)
    if body.role_ids is not None:
        await _check_roles(session, body.role_ids)
        await set_user_roles(session, target.id, body.role_ids)
    if body.password:
        if target.password_hash is None:
            raise AppError("Tài khoản SSO không có mật khẩu trong infra-ops.", code="not_local")
        target.password_hash = _check_password(body.password)
        await destroy_user_sessions(session, target.id)
    if body.unlock:
        target.locked_until = None
        target.failed_logins = 0
    await session.flush()
    if await count_active_admins(session) == 0:
        raise AppError("Phải còn ít nhất một quản trị viên đang hoạt động.", code="last_admin")
    after = (await _user_out(session, target)).model_dump(
        mode="json", include={"display_name", "is_active", "role_ids"}
    )
    if body.password:
        after["password_reset"] = True
    if body.unlock:
        after["unlocked"] = True
    await record(
        session,
        actor=user.actor,
        module_id="core",
        action="rbac.user_updated",
        target_type="user",
        target_id=str(target.id),
        target_label=target.upn,
        before=before,
        after=after,
    )
    out = await _user_out(session, target)
    await session.commit()
    return out
