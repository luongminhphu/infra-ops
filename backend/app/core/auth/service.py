"""Login flows shared by local, OIDC and dev modes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor, record
from app.core.auth.models import User
from app.core.auth.oidc import OidcIdentity
from app.core.auth.passwords import DUMMY_HASH, verify_password
from app.core.config import Settings
from app.core.errors import NotAuthenticated
from app.core.rbac.service import grant_role

INVALID_LOGIN = "Tên đăng nhập hoặc mật khẩu không đúng."


async def find_user_by_upn(session: AsyncSession, upn: str) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.upn) == upn.strip().lower()))


async def _bootstrap(session: AsyncSession, settings: Settings, user: User) -> None:
    if user.upn.lower() in settings.bootstrap_admin_upns:
        await grant_role(session, user.id, "admin")


async def login_local(
    session: AsyncSession, settings: Settings, username: str, password: str, ip: str | None
) -> User:
    # Row lock: parallel attempts must not lose failed-login increments (lockout bypass).
    user = await session.scalar(
        select(User).where(func.lower(User.upn) == username.strip().lower()).with_for_update()
    )
    now = datetime.now(UTC)
    if user is None or user.password_hash is None:
        await asyncio.to_thread(verify_password, password, DUMMY_HASH)  # equalise timing
        await record(
            session,
            actor=Actor(None, username[:320], ip),
            module_id="core",
            action="auth.login",
            result="failure",
            message="unknown user or not a local account",
        )
        raise NotAuthenticated(INVALID_LOGIN)
    if user.locked_until and user.locked_until > now:
        await record(
            session,
            actor=Actor(user.id, user.upn, ip),
            module_id="core",
            action="auth.login",
            result="denied",
            message="account locked",
        )
        minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        raise NotAuthenticated(f"Tài khoản tạm khoá do đăng nhập sai nhiều lần. Thử lại sau {minutes} phút.")
    if not user.is_active or not await asyncio.to_thread(verify_password, password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= settings.local_max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.local_lockout_minutes)
            user.failed_logins = 0
        await record(
            session,
            actor=Actor(user.id, user.upn, ip),
            module_id="core",
            action="auth.login",
            result="failure",
            message="invalid password or inactive",
        )
        raise NotAuthenticated(INVALID_LOGIN)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    await _bootstrap(session, settings, user)
    await record(session, actor=Actor(user.id, user.upn, ip), module_id="core", action="auth.login")
    return user


async def login_oidc(session: AsyncSession, settings: Settings, ident: OidcIdentity, ip: str | None) -> User:
    user = await session.scalar(select(User).where(User.entra_oid == ident.oid))
    if user is None:
        # Admins may pre-create users by UPN to assign roles before first login.
        user = await find_user_by_upn(session, ident.upn)
        if user is not None and user.entra_oid is not None:
            # The UPN belongs to another Entra identity (e.g. a recycled UPN): never take over that account.
            await record(
                session,
                actor=Actor(user.id, user.upn, ip),
                module_id="core",
                action="auth.login",
                result="denied",
                message="upn linked to another entra oid",
            )
            raise NotAuthenticated(
                "Tài khoản này đã gắn với một danh tính Microsoft khác. Vui lòng liên hệ Quản trị viên."
            )
        if user is None:
            user = User(upn=ident.upn, display_name=ident.display_name, email=ident.email)
            session.add(user)
            await session.flush()
            # First SSO login: read-only until an admin assigns Manager/Operator in the app.
            await grant_role(session, user.id, "viewer")
        user.entra_oid = ident.oid
    if not user.is_active:
        await record(
            session,
            actor=Actor(user.id, user.upn, ip),
            module_id="core",
            action="auth.login",
            result="denied",
            message="inactive user",
        )
        raise NotAuthenticated("Tài khoản đã bị vô hiệu hoá trong infra-ops.")
    user.display_name = ident.display_name or user.display_name
    user.email = ident.email or user.email
    user.last_login_at = datetime.now(UTC)
    await session.flush()
    await _bootstrap(session, settings, user)
    await record(
        session, actor=Actor(user.id, user.upn, ip), module_id="core", action="auth.login", message="oidc"
    )
    return user


async def login_dev(session: AsyncSession, settings: Settings, upn: str, ip: str | None) -> User:
    user = await find_user_by_upn(session, upn)
    if user is None:
        user = User(upn=upn.lower(), display_name=upn.split("@")[0])
        session.add(user)
        await session.flush()
    user.last_login_at = datetime.now(UTC)
    await _bootstrap(session, settings, user)
    await record(
        session, actor=Actor(user.id, user.upn, ip), module_id="core", action="auth.login", message="dev mode"
    )
    return user
