from __future__ import annotations

import asyncio

import httpx
import jwt
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth import oidc
from app.core.auth.deps import CurrentUser, client_ip, current_user
from app.core.auth.models import User
from app.core.auth.passwords import hash_password, password_problems, verify_password
from app.core.auth.service import login_dev, login_local, login_oidc
from app.core.auth.sessions import (
    clear_session_cookie,
    create_session,
    destroy_session,
    destroy_user_sessions,
    set_session_cookie,
)
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AppError, NotAuthenticated
from app.core.logging import get_logger
from app.core.modules.registry import get_registry
from app.core.rbac.service import role_codes_for_user

router = APIRouter(tags=["auth"])
log = get_logger(__name__)


class AuthConfig(BaseModel):
    mode: str
    # Secure session cookie: browsers drop it on plain http://, so the login page warns before signing in.
    cookie_secure: bool
    # SSO mode with local accounts allowed: the login page offers both.
    local_login: bool


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(default="", max_length=256)


@router.get("/auth/config", response_model=AuthConfig)
async def auth_config() -> AuthConfig:
    settings = get_settings()
    return AuthConfig(
        mode=settings.auth_mode,
        cookie_secure=settings.session_cookie_secure,
        local_login=settings.local_login,
    )


@router.post("/auth/login", status_code=204)
async def login(
    body: LoginBody, request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> None:
    settings = get_settings()
    ip = client_ip(request)
    try:
        if settings.local_login:
            user = await login_local(session, settings, body.username, body.password, ip)
        elif settings.auth_mode == "dev":
            user = await login_dev(session, settings, body.username, ip)
        else:
            raise AppError("Hệ thống dùng đăng nhập SSO Microsoft.", code="use_sso")
    except NotAuthenticated:
        await session.commit()  # persist failed-attempt counters and audit
        raise
    token, _ = await create_session(session, settings, user, ip, request.headers.get("user-agent"))
    await session.commit()
    set_session_cookie(response, settings, token)


@router.get("/auth/oidc/login")
async def oidc_login(next: str = "/") -> RedirectResponse:
    settings = get_settings()
    if settings.auth_mode != "oidc":
        raise AppError("SSO chưa được bật.", code="sso_disabled")
    url, cookie = oidc.build_authorize(settings, next)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        oidc.STATE_COOKIE,
        cookie,
        max_age=oidc.STATE_MAX_AGE,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/api/core/auth/oidc",
    )
    return resp


@router.get("/auth/oidc/callback")
async def oidc_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    failed = RedirectResponse("/login?error=sso", status_code=302)
    failed.delete_cookie(oidc.STATE_COOKIE, path="/api/core/auth/oidc")
    if error or not code:
        log.warning("oidc_callback_error", error=error[:200])
        return failed
    ip = client_ip(request)
    try:
        state_data = oidc.read_state(settings, request.cookies.get(oidc.STATE_COOKIE), state)
        ident = await oidc.exchange_code(settings, code, state_data)
    # ValueError: a token response that is not JSON.
    except (AppError, httpx.HTTPError, jwt.PyJWTError, ValueError) as exc:
        log.warning("oidc_callback_failed", error=type(exc).__name__, detail=str(exc)[:200])
        return failed
    try:
        user = await login_oidc(session, settings, ident, ip)
    except NotAuthenticated as exc:
        await session.commit()  # keep the denied-login audit entry
        log.warning("oidc_login_denied", upn=ident.upn, detail=exc.message)
        return failed
    token, _ = await create_session(session, settings, user, ip, request.headers.get("user-agent"))
    await session.commit()
    resp = RedirectResponse(state_data.get("next", "/"), status_code=302)
    set_session_cookie(resp, settings, token)
    resp.delete_cookie(oidc.STATE_COOKIE, path="/api/core/auth/oidc")
    return resp


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await destroy_session(session, token)
    await record(session, actor=user.actor, module_id="core", action="auth.logout")
    await session.commit()
    clear_session_cookie(response, settings)


class MeOut(BaseModel):
    id: str
    upn: str
    display_name: str
    roles: list[str]
    permissions: list[str]
    csrf_token: str
    auth_mode: str
    local_login: bool
    is_local: bool
    nav: list[dict[str, object]]


@router.get("/me", response_model=MeOut)
async def me(
    user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> MeOut:
    model = await session.get(User, user.id)
    return MeOut(
        id=str(user.id),
        upn=user.upn,
        display_name=user.display_name,
        roles=await role_codes_for_user(session, user.id),
        permissions=sorted(user.permissions),
        csrf_token=user.csrf_token,
        auth_mode=get_settings().auth_mode,
        local_login=get_settings().local_login,
        is_local=bool(model and model.password_hash),
        nav=get_registry().nav_for(user.permissions),
    )


class ChangePasswordBody(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


@router.post("/me/password", status_code=204)
async def change_password(
    body: ChangePasswordBody,
    request: Request,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    settings = get_settings()
    model = await session.get(User, user.id)
    if model is None or model.password_hash is None:
        raise AppError("Tài khoản SSO không đổi mật khẩu tại đây.", code="not_local")
    if not await asyncio.to_thread(verify_password, body.current_password, model.password_hash):
        raise AppError("Mật khẩu hiện tại không đúng.", code="invalid_password")
    problems = password_problems(body.new_password, settings.local_min_password_length)
    if problems:
        raise AppError(" ".join(problems), code="weak_password")
    model.password_hash = await asyncio.to_thread(hash_password, body.new_password)
    # Sign out every other device; keep the session making this request.
    await destroy_user_sessions(
        session, user.id, keep_token=request.cookies.get(settings.session_cookie_name)
    )
    await record(
        session,
        actor=user.actor,
        module_id="core",
        action="auth.password_changed",
        target_type="user",
        target_id=str(user.id),
        target_label=user.upn,
    )
    await session.commit()
