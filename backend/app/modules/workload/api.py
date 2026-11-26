"""Helpdesk/Support tasks from SDP: per-operator lists, reassignment and statistics."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.auth.models import User
from app.core.connections.models import ConnectionProfile
from app.core.db import get_session
from app.core.errors import AppError, NotFound
from app.core.scheduler.service import get_task, request_run, task_out
from app.core.settings.service import save_settings
from app.core.timeutil import TZ
from app.core.workflow.api import job_out
from app.core.workflow.engine import create_job, start_job
from app.modules.workload.models import Technician, Ticket
from app.modules.workload.po import repack
from app.modules.workload.rules import pick_bucket
from app.modules.workload.settings import MODULE_ID, SETTINGS_KEY, WorkloadSettings
from app.modules.workload.settings import load as load_module_settings
from app.modules.workload.sync import SYNC_TASK_KEY, auto_link, create_account, reclassify

router = APIRouter(tags=["workload"])
view = require("workload.tickets.view")
assign = require("workload.tickets.assign")
stats_perm = require("workload.stats.view")
sync_perm = require("workload.sync.run")
manage = require("workload.settings.manage")

State = Literal["open", "pending", "in_progress", "on_hold", "overdue", "done", "cancelled", "all"]


def _now() -> datetime:
    return datetime.now(UTC)


def ticket_out(t: Ticket, now: datetime) -> dict[str, Any]:
    late = t.due_by is not None and (
        (t.state == "open" and t.due_by < now)
        or (t.state == "done" and t.completed_at is not None and t.completed_at > t.due_by)
    )
    return {
        "id": t.id,
        "display_id": t.display_id,
        "subject": t.subject,
        "requester_name": t.requester_name,
        "technician_id": t.technician_id,
        "technician_name": t.technician_name,
        "group_name": t.group_name,
        "status": t.status,
        "state": t.state,
        "phase": t.phase,
        "device_need": t.device_need,
        "po_dismissed": t.po_dismissed,
        "priority": t.priority,
        "created_at": t.created_at,
        "due_by": t.due_by,
        "completed_at": t.completed_at,
        "overdue": late,
    }


VIEW_ALL = "workload.tickets.view_all"
NO_TECHNICIAN = "__none__"  # matches no ticket


async def _my_technician(session: AsyncSession, user: CurrentUser) -> Technician | None:
    return await session.scalar(select(Technician).where(Technician.user_id == user.id))


async def _scope(session: AsyncSession, user: CurrentUser, requested: str) -> str:
    """Technician filter the user may use: operators without view_all only ever see their own tasks."""
    if requested == "me" or not user.has(VIEW_ALL):
        me = await _my_technician(session, user)
        return me.id if me else NO_TECHNICIAN
    return requested


# ---------------------------------------------------------------- technicians (operators)
@router.get("/technicians")
async def technicians(
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    now = _now()
    counts = {
        tid: (o, od)
        for tid, o, od in (
            await session.execute(
                select(
                    Ticket.technician_id,
                    func.count(),
                    func.count().filter(Ticket.due_by < now),
                )
                .where(Ticket.state == "open")
                .group_by(Ticket.technician_id)
            )
        ).all()
    }
    rows = (
        await session.execute(
            select(Technician, User).outerjoin(User, User.id == Technician.user_id).order_by(Technician.name)
        )
    ).all()
    me = await _my_technician(session, user)
    if not user.has(VIEW_ALL):
        rows = [(t, u) for t, u in rows if me is not None and t.id == me.id]
    items = [
        {
            "id": t.id,
            "name": t.name,
            "email": t.email,
            "active": t.active,
            "user": {"id": str(u.id), "upn": u.upn, "display_name": u.display_name} if u else None,
            "mapped_manually": t.mapped_manually,
            "open": counts.get(t.id, (0, 0))[0],
            "overdue": counts.get(t.id, (0, 0))[1],
        }
        for t, u in rows
    ]
    unassigned = counts.get(None, (0, 0)) if user.has(VIEW_ALL) else (0, 0)
    return {
        "items": items,
        "unassigned": {"open": unassigned[0], "overdue": unassigned[1]},
        "me": me.id if me else None,
        "view_all": user.has(VIEW_ALL),
    }


class LinkIn(BaseModel):
    user_id: uuid.UUID | None = None


@router.put("/technicians/{technician_id}/user")
async def link_user(
    technician_id: str,
    body: LinkIn,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    tech = await session.get(Technician, technician_id)
    if tech is None:
        raise NotFound("Không tìm thấy technician.")
    if body.user_id is not None:
        if await session.get(User, body.user_id) is None:
            raise NotFound("Không tìm thấy người dùng.")
        other = await session.scalar(
            select(Technician).where(Technician.user_id == body.user_id, Technician.id != tech.id)
        )
        if other is not None:
            raise AppError(
                f"Người dùng này đã liên kết với technician {other.name}.", code="user_already_linked"
            )
    before = {"user_id": str(tech.user_id) if tech.user_id else None}
    tech.user_id = body.user_id
    # Clearing the link by hand also stops automatic e-mail matching for this technician.
    tech.mapped_manually = True
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.technician_linked",
        target_type="technician",
        target_id=tech.id,
        target_label=tech.name,
        before=before,
        after={"user_id": str(body.user_id) if body.user_id else None},
    )
    await session.commit()
    return {"id": tech.id, "user_id": str(tech.user_id) if tech.user_id else None}


@router.post("/technicians/{technician_id}/account", status_code=201)
async def confirm_account(
    technician_id: str, user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """An administrator confirms an SDP technician as an infra-ops user (Operator, SSO by e-mail)."""
    tech = await session.get(Technician, technician_id)
    if tech is None:
        raise NotFound("Không tìm thấy technician.")
    if not tech.email:
        raise AppError(
            "Technician chưa có e-mail trên SDP nên không tạo được tài khoản SSO.", code="no_email"
        )
    if tech.user_id:
        raise AppError("Technician đã có tài khoản infra-ops.", code="already_linked")
    account = await create_account(session, tech, user.actor)
    await session.commit()
    return {"id": tech.id, "user_id": str(account.id), "upn": account.upn}


@router.delete("/technicians/{technician_id}/user", status_code=204)
async def reset_link(
    technician_id: str, user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> None:
    """Return to automatic e-mail matching."""
    tech = await session.get(Technician, technician_id)
    if tech is None:
        raise NotFound("Không tìm thấy technician.")
    tech.mapped_manually, tech.user_id = False, None
    await session.flush()
    await auto_link(session, [tech])
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.technician_link_reset",
        target_type="technician",
        target_id=tech.id,
        target_label=tech.name,
    )
    await session.commit()


# ---------------------------------------------------------------- tickets
@router.get("/tickets")
async def tickets(
    user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    state: State = "open",
    technician_id: str = Query("", max_length=32, description="'me', 'none' (unassigned) or an id"),
    q: str = Query("", max_length=100),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    now = _now()
    stmt = select(Ticket)
    if state == "overdue":
        stmt = stmt.where(Ticket.state == "open", Ticket.due_by < now)
    elif state in ("pending", "in_progress", "on_hold"):
        stmt = stmt.where(Ticket.state == "open", Ticket.phase == state)
    elif state != "all":
        stmt = stmt.where(Ticket.state == state)
    technician_id = await _scope(session, user, technician_id)
    if technician_id == "none":
        stmt = stmt.where(Ticket.technician_id.is_(None))
    elif technician_id:
        stmt = stmt.where(Ticket.technician_id == technician_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Ticket.subject).like(like),
                Ticket.display_id.like(like),
                func.lower(Ticket.requester_name).like(like),
            )
        )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    order = Ticket.completed_at.desc() if state in ("done", "cancelled") else Ticket.due_by.asc().nulls_last()
    rows = (await session.scalars(stmt.order_by(order, Ticket.created_at.desc()).limit(limit))).all()
    return {"total": total, "items": [ticket_out(t, now) for t in rows]}


class ReassignIn(BaseModel):
    ticket_ids: list[str] = Field(min_length=1, max_length=200)
    to_technician_id: str = Field(min_length=1, max_length=32)
    reason: str = Field("", max_length=500)


@router.post("/tickets/reassign", status_code=201)
async def reassign(
    body: ReassignIn, user: CurrentUser = Depends(assign), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    target = await session.get(Technician, body.to_technician_id)
    if target is None or not target.active:
        raise NotFound("Không tìm thấy người nhận việc trên SDP.")
    ids = list(dict.fromkeys(body.ticket_ids))
    found = (await session.scalars(select(Ticket).where(Ticket.id.in_(ids)))).all()
    if len(found) != len(ids):
        raise NotFound("Có request không còn trong dữ liệu đồng bộ, hãy tải lại danh sách.")
    if any(t.state != "open" for t in found):
        raise AppError("Chỉ chuyển được request đang mở.", code="ticket_not_open")
    job, _ = await create_job(
        session,
        user.actor,
        "workload.reassign",
        params={
            "ticket_ids": ids,
            "to_technician_id": target.id,
            "to_technician_name": target.name,
            "from": {t.id: t.technician_name for t in found},
            "reason": body.reason,
        },
        subject_key=target.id,
        subject_label=f"Chuyển {len(ids)} request cho {target.name}",
        external_ref=None,
    )
    await start_job(session, user.actor, job, None)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.reassign_requested",
        system_type="sdp",
        target_type="job",
        target_id=str(job.id),
        target_label=job.subject_label,
        before={"technicians": {t.id: t.technician_name for t in found}},
        after={"technician": target.name},
        message=body.reason or None,
    )
    await session.commit()
    return job_out(job)


# ---------------------------------------------------------------- statistics
@router.get("/stats")
async def stats(
    user: CurrentUser = Depends(stats_perm),
    session: AsyncSession = Depends(get_session),
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    technician_id: str = Query("", max_length=32),
) -> dict[str, Any]:
    if date_to < date_from or (date_to - date_from).days > 1100:
        raise AppError("Khoảng thời gian không hợp lệ (tối đa 3 năm).", code="bad_period")
    start = datetime.combine(date_from, time.min, TZ)
    end = datetime.combine(date_to + timedelta(days=1), time.min, TZ)
    now = _now()
    bucket = pick_bucket((date_to - date_from).days + 1)
    technician_id = await _scope(session, user, technician_id)
    scope = [Ticket.technician_id == technician_id] if technician_id else []

    created_in = and_(Ticket.created_at >= start, Ticket.created_at < end)
    done_in = and_(Ticket.state == "done", Ticket.completed_at >= start, Ticket.completed_at < end)
    late = and_(done_in, Ticket.due_by.is_not(None), Ticket.completed_at > Ticket.due_by)
    open_now = Ticket.state == "open"
    hours = func.extract("epoch", Ticket.completed_at - Ticket.created_at) / 3600
    cols = (
        func.count().filter(created_in).label("received"),
        func.count().filter(done_in).label("completed"),
        func.count().filter(late).label("completed_late"),
        func.avg(case((done_in, hours))).label("avg_hours"),
        func.count().filter(open_now).label("open"),
        func.count().filter(and_(open_now, Ticket.due_by < now)).label("overdue"),
    )
    relevant = or_(created_in, done_in, open_now)

    def row(r: Any) -> dict[str, Any]:
        return {
            "received": r.received,
            "completed": r.completed,
            "completed_late": r.completed_late,
            "completed_on_time": r.completed - r.completed_late,
            "on_time_rate": round((r.completed - r.completed_late) / r.completed, 4) if r.completed else None,
            "avg_hours": round(float(r.avg_hours), 1) if r.avg_hours is not None else None,
            "open": r.open,
            "overdue": r.overdue,
        }

    total = (await session.execute(select(*cols).where(relevant, *scope))).one()
    per = (
        await session.execute(
            select(Ticket.technician_id, func.max(Ticket.technician_name).label("name"), *cols)
            .where(relevant, *scope)
            .group_by(Ticket.technician_id)
        )
    ).all()
    by_tech = sorted(
        ({"technician_id": r.technician_id, "name": r.name, **row(r)} for r in per),
        key=lambda x: (-x["received"], x["name"] or ""),
    )

    async def series(ts: Any, cond: Any) -> dict[str, int]:
        b = func.date_trunc(bucket, func.timezone("Asia/Ho_Chi_Minh", ts)).label("b")
        rows = (await session.execute(select(b, func.count()).where(cond, *scope).group_by(b))).all()
        return {d.date().isoformat(): n for d, n in rows}

    received = await series(Ticket.created_at, created_in)
    completed = await series(Ticket.completed_at, done_in)
    keys = sorted(set(received) | set(completed))
    return {
        "from": date_from,
        "to": date_to,
        "bucket": bucket,
        "totals": row(total),
        "technicians": by_tech,
        "series": [
            {"period": k, "received": received.get(k, 0), "completed": completed.get(k, 0)} for k in keys
        ],
    }


# ---------------------------------------------------------------- sync + settings
@router.get("/sync")
async def sync_status(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    task = await get_task(session, SYNC_TASK_KEY)
    last = await session.scalar(select(func.max(Ticket.synced_at)))
    return {"task": task_out(task) if task else None, "last_synced_at": last}


@router.post("/sync")
async def sync_now(
    user: CurrentUser = Depends(sync_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    row = await request_run(session, SYNC_TASK_KEY, user.upn)
    await record(session, actor=user.actor, module_id=MODULE_ID, action="workload.sync_requested")
    await session.commit()
    return task_out(row)


@router.get("/settings")
async def get_settings_(
    _: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = await load_module_settings(session)
    profiles = (
        await session.scalars(
            select(ConnectionProfile)
            .where(ConnectionProfile.system_type == "sdp")
            .order_by(ConnectionProfile.name)
        )
    ).all()
    return {
        "values": settings.model_dump(mode="json"),
        "profiles": [
            {"id": str(p.id), "name": p.name, "environment": p.environment, "enabled": p.enabled}
            for p in profiles
        ],
    }


@router.put("/settings")
async def put_settings(
    body: dict[str, Any], user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = await save_settings(session, user.actor, MODULE_ID, SETTINGS_KEY, WorkloadSettings, body)
    # Status lists and device keywords changed: apply them to every stored ticket now.
    await reclassify(session, settings, only_missing=False)
    await repack(session, settings)  # the PO limit may have changed
    await session.commit()
    return {"values": settings.model_dump(mode="json")}


@router.get("/users")
async def linkable_users(
    _: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    users = (
        await session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name))
    ).all()
    return [{"id": str(u.id), "upn": u.upn, "display_name": u.display_name} for u in users]
