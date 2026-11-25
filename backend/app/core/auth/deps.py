"""Request dependencies: current user, CSRF and permission checks."""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor
from app.core.auth.sessions import load_session
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import NotAuthenticated, PermissionDenied
from app.core.modules.registry import get_registry
from app.core.rbac.service import permissions_for_user

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class CurrentUser:
    id: uuid.UUID
    upn: str
    display_name: str
    permissions: set[str] = field(default_factory=set)
    csrf_token: str = ""
    ip: str | None = None

    def has(self, code: str) -> bool:
        return code in self.permissions

    @property
    def actor(self) -> Actor:
        return Actor(user_id=self.id, upn=self.upn, ip=self.ip)


def client_ip(request: Request) -> str | None:
    # Clipped to the String(64) audit/session columns; the value may come from X-Forwarded-For.
    return request.client.host[:64] if request.client else None


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> CurrentUser:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise NotAuthenticated("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.")
    loaded = await load_session(session, token)
    if loaded is None:
        raise NotAuthenticated("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.")
    sess, user = loaded
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(sent, sess.csrf_token):
            raise PermissionDenied(
                "Yêu cầu không hợp lệ (CSRF). Tải lại trang và thử lại.", code="csrf_failed"
            )
    perms = await permissions_for_user(session, user.id, get_registry())
    return CurrentUser(
        id=user.id,
        upn=user.upn,
        display_name=user.display_name,
        permissions=perms,
        csrf_token=sess.csrf_token,
        ip=client_ip(request),
    )


def require(*codes: str) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """Dependency factory: user must hold every listed permission."""

    async def _dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        missing = [c for c in codes if not user.has(c)]
        if missing:
            raise PermissionDenied("Bạn không có quyền thực hiện thao tác này.", details={"missing": missing})
        return user

    return _dep
