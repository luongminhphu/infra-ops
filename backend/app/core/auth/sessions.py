"""Server-side sessions: random token in an httpOnly cookie, SHA-256 stored in DB."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User, UserSession
from app.core.config import Settings


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(
    session: AsyncSession, settings: Settings, user: User, ip: str | None, user_agent: str | None
) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    row = UserSession(
        token_hash=_hash(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(24),
        expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
        ip=ip,
        user_agent=(user_agent or "")[:512],
    )
    session.add(row)
    # Opportunistic cleanup of expired sessions.
    await session.execute(delete(UserSession).where(UserSession.expires_at < datetime.now(UTC)))
    await session.flush()
    return token, row


async def load_session(session: AsyncSession, token: str) -> tuple[UserSession, User] | None:
    row = await session.scalar(
        select(UserSession).where(
            UserSession.token_hash == _hash(token), UserSession.expires_at > datetime.now(UTC)
        )
    )
    if row is None:
        return None
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return row, user


async def destroy_session(session: AsyncSession, token: str) -> None:
    await session.execute(delete(UserSession).where(UserSession.token_hash == _hash(token)))


async def destroy_user_sessions(
    session: AsyncSession, user_id: uuid.UUID, keep_token: str | None = None
) -> None:
    stmt = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token:
        stmt = stmt.where(UserSession.token_hash != _hash(keep_token))
    await session.execute(stmt)


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
