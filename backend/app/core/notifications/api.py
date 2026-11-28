"""Thông báo: personal e-mail digest switch (anyone signed in) and platform settings (administrators)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser, require
from app.core.auth.models import User
from app.core.connections.models import ConnectionProfile
from app.core.db import get_session
from app.core.notifications import service
from app.core.permissions import DASHBOARD_VIEW, NOTIFICATIONS_MANAGE

router = APIRouter(tags=["notifications"])


class PrefIn(BaseModel):
    email_digest: bool


@router.get("/me/notifications")
async def my_preference(
    user: CurrentUser = Depends(require(DASHBOARD_VIEW)), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    cfg = await service.load(session)
    row = await session.get(User, user.id)
    return {
        "has_email": bool(row and row.email),  # without an address nothing can be mailed
        "email_digest": await service.get_pref(session, user.id),
        "enabled": cfg.enabled,  # whether the platform switch is on, so the person knows mail is flowing
        "times": cfg.times,
    }


@router.put("/me/notifications")
async def set_my_preference(
    body: PrefIn,
    user: CurrentUser = Depends(require(DASHBOARD_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    value = await service.set_pref(session, user.actor, body.email_digest)
    await session.commit()
    return {"email_digest": value}


@router.get("/notifications/settings")
async def get_settings(
    _: CurrentUser = Depends(require(NOTIFICATIONS_MANAGE)), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    return (await service.load(session)).model_dump(mode="json")


@router.put("/notifications/settings")
async def put_settings(
    body: dict[str, Any],
    user: CurrentUser = Depends(require(NOTIFICATIONS_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    saved = await service.save(session, user.actor, body)
    await session.commit()
    return saved.model_dump(mode="json")


@router.get("/notifications/profiles")
async def profiles(
    _: CurrentUser = Depends(require(NOTIFICATIONS_MANAGE)), session: AsyncSession = Depends(get_session)
) -> dict[str, list[dict[str, str]]]:
    """SMTP / Teams profiles to choose from (id + name only), for people who may not open Kết nối hệ thống."""
    rows = (
        await session.execute(
            select(ConnectionProfile.system_type, ConnectionProfile.id, ConnectionProfile.name)
            .where(ConnectionProfile.system_type.in_(["smtp", "teams"]))
            .order_by(ConnectionProfile.name)
        )
    ).all()
    return {
        t: [{"id": str(r.id), "name": r.name} for r in rows if r.system_type == t] for t in ("smtp", "teams")
    }
