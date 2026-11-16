from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.models import AuditLog
from app.core.audit.service import verify_chain
from app.core.auth.deps import CurrentUser, require
from app.core.db import get_session
from app.core.permissions import AUDIT_VIEW

router = APIRouter(tags=["audit"])
view = require(AUDIT_VIEW)


class AuditOut(BaseModel):
    id: int
    at: datetime
    actor_upn: str | None
    actor_ip: str | None
    module_id: str
    system_type: str | None
    action: str
    target_type: str | None
    target_id: str | None
    target_label: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    result: str
    message: str | None
    correlation_id: str | None


class AuditPage(BaseModel):
    items: list[AuditOut]
    next_before_id: int | None


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    _: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    module: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    result: Literal["success", "failure", "denied"] | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    before_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> AuditPage:
    stmt = select(AuditLog)
    if module:
        stmt = stmt.where(AuditLog.module_id == module)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor:
        stmt = stmt.where(func.lower(AuditLog.actor_upn).contains(actor.lower()))
    if result:
        stmt = stmt.where(AuditLog.result == result)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(AuditLog.target_label).like(like),
                func.lower(AuditLog.message).like(like),
                func.lower(AuditLog.action).like(like),
            )
        )
    if date_from:
        stmt = stmt.where(AuditLog.at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.at < date_to)
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)
    rows = (await session.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit + 1))).all()
    items = [AuditOut.model_validate(r, from_attributes=True) for r in rows[:limit]]
    return AuditPage(items=items, next_before_id=items[-1].id if len(rows) > limit else None)


@router.get("/audit/facets")
async def audit_facets(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, list[str]]:
    module_stmt = select(AuditLog.module_id).distinct().order_by(AuditLog.module_id)
    modules = (await session.scalars(module_stmt)).all()
    actions = (await session.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action))).all()
    return {"modules": list(modules), "actions": list(actions)}


@router.get("/audit/verify")
async def audit_verify(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    report = await verify_chain(session)
    return {
        "ok": report.ok,
        "checked": report.checked,
        "broken_at_id": report.broken_at_id,
        "reason": report.reason,
    }
