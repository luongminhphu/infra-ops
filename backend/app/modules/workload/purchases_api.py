"""Device purchase proposals: suggested from SDP requests about replacing, upgrading or repairing a
device, kept in infra-ops and exported for the purchasing team (nothing is written to SDP)."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.db import get_session
from app.core.errors import AppError, NotFound
from app.modules.workload.api import VIEW_ALL, _my_technician, ticket_out
from app.modules.workload.models import PurchaseRequest, Ticket
from app.modules.workload.settings import MODULE_ID

router = APIRouter(tags=["workload"])
view = require("workload.tickets.view")
create = require("workload.purchase.create")
manage = require("workload.purchase.manage")

Kind = Literal["replace", "upgrade", "repair", "new"]
Status = Literal["proposed", "approved", "ordered", "received", "rejected"]
KIND_LABEL = {"replace": "Thay thế", "upgrade": "Nâng cấp", "repair": "Sửa chữa", "new": "Cấp mới"}
STATUS_LABEL = {
    "proposed": "Đã đề xuất",
    "approved": "Đã duyệt",
    "ordered": "Đã đặt mua",
    "received": "Đã nhận hàng",
    "rejected": "Từ chối",
}
# Allowed status changes (a manager decides; nothing goes backwards except a re-opened rejection).
NEXT: dict[str, set[str]] = {
    "proposed": {"approved", "rejected"},
    "approved": {"ordered", "rejected"},
    "ordered": {"received"},
    "received": set(),
    "rejected": {"proposed"},
}


def out(p: PurchaseRequest) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "ticket_id": p.ticket_id,
        "ticket_display_id": p.ticket_display_id,
        "kind": p.kind,
        "item": p.item,
        "quantity": p.quantity,
        "reason": p.reason,
        "status": p.status,
        "note": p.note,
        "created_by": p.created_by,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "next": sorted(NEXT[p.status]),
    }


def _scoped(user: CurrentUser) -> Any:
    stmt = select(PurchaseRequest)
    return stmt if user.has(VIEW_ALL) else stmt.where(PurchaseRequest.created_by == user.upn)


@router.get("/purchases")
async def list_purchases(
    user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    status: str = Query("", max_length=16),
) -> dict[str, Any]:
    stmt = _scoped(user)
    if status:
        stmt = stmt.where(PurchaseRequest.status == status)
    rows = (await session.scalars(stmt.order_by(PurchaseRequest.created_at.desc()).limit(1000))).all()
    scoped = _scoped(user).subquery()
    counts = dict(
        (await session.execute(select(scoped.c.status, func.count()).group_by(scoped.c.status))).all()
    )
    return {"items": [out(p) for p in rows], "counts": counts}


class PurchaseIn(BaseModel):
    ticket_id: str | None = Field(None, max_length=32)
    kind: Kind
    item: str = Field(min_length=2, max_length=300)
    quantity: int = Field(1, ge=1, le=500)
    reason: str = Field("", max_length=2000)


@router.post("/purchases", status_code=201)
async def create_purchase(
    body: PurchaseIn, user: CurrentUser = Depends(create), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    display = None
    if body.ticket_id:
        ticket = await session.get(Ticket, body.ticket_id)
        if ticket is None:
            raise NotFound("Không tìm thấy request.")
        display = ticket.display_id
    p = PurchaseRequest(
        ticket_id=body.ticket_id,
        ticket_display_id=display,
        kind=body.kind,
        item=body.item.strip(),
        quantity=body.quantity,
        reason=body.reason.strip(),
        status="proposed",
        created_by=user.upn,
    )
    session.add(p)
    await session.flush()
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.purchase_created",
        target_type="purchase",
        target_id=str(p.id),
        target_label=p.item,
        after={"kind": p.kind, "quantity": p.quantity, "ticket": display},
    )
    await session.commit()
    await session.refresh(p)
    return out(p)


class StatusIn(BaseModel):
    status: Status
    note: str = Field("", max_length=1000)


@router.patch("/purchases/{purchase_id}")
async def update_purchase(
    purchase_id: uuid.UUID,
    body: StatusIn,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    p = await session.get(PurchaseRequest, purchase_id, with_for_update=True)
    if p is None:
        raise NotFound("Không tìm thấy đề xuất.")
    if body.status not in NEXT[p.status]:
        raise AppError(
            f"Không chuyển được từ “{STATUS_LABEL[p.status]}” sang “{STATUS_LABEL[body.status]}”.",
            code="invalid_transition",
        )
    if body.status == "rejected" and len(body.note.strip()) < 3:
        raise AppError("Ghi lý do từ chối.", code="reason_required")
    before = p.status
    p.status, p.updated_at = body.status, datetime.now(UTC)
    if body.note.strip():
        p.note = body.note.strip()
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.purchase_status",
        target_type="purchase",
        target_id=str(p.id),
        target_label=p.item,
        before={"status": before},
        after={"status": p.status, "note": body.note.strip() or None},
    )
    await session.commit()
    await session.refresh(p)
    return out(p)


@router.post("/tickets/{ticket_id}/dismiss-purchase", status_code=204)
async def dismiss_suggestion(
    ticket_id: str, user: CurrentUser = Depends(create), session: AsyncSession = Depends(get_session)
) -> None:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFound("Không tìm thấy request.")
    ticket.po_dismissed = True
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.purchase_dismissed",
        target_type="ticket",
        target_id=ticket.id,
        target_label=ticket.subject[:200],
    )
    await session.commit()


@router.get("/purchases/export")
async def export_purchases(
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> Response:
    rows = (await session.scalars(_scoped(user).order_by(PurchaseRequest.created_at))).all()
    wb = Workbook()
    ws = wb.active
    ws.title = "De xuat mua thiet bi"
    ws.append(
        [
            "Ngày",
            "Request SDP",
            "Loại",
            "Thiết bị",
            "Số lượng",
            "Lý do",
            "Trạng thái",
            "Ghi chú",
            "Người đề xuất",
        ]
    )
    for p in rows:
        ws.append(
            [
                p.created_at.astimezone(UTC).strftime("%d/%m/%Y"),
                f"#{p.ticket_display_id}" if p.ticket_display_id else "",
                KIND_LABEL.get(p.kind, p.kind),
                p.item,
                p.quantity,
                p.reason,
                STATUS_LABEL.get(p.status, p.status),
                p.note,
                p.created_by,
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="de-xuat-mua-thiet-bi.xlsx"'},
    )


# ---------------------------------------------------------------- operator portal
@router.get("/my-work")
async def my_work(
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """The signed-in operator's own SDP tasks by phase, recent completions and purchase suggestions."""
    me = await _my_technician(session, user)
    if me is None:
        return {"linked": False}
    now = datetime.now(UTC)
    mine = Ticket.technician_id == me.id
    counts = dict(
        (
            await session.execute(
                select(Ticket.phase, func.count()).where(mine, Ticket.state == "open").group_by(Ticket.phase)
            )
        ).all()
    )
    overdue = await session.scalar(
        select(func.count()).where(mine, Ticket.state == "open", Ticket.due_by < now)
    )
    week = now - timedelta(days=7)
    done = (
        await session.scalars(
            select(Ticket)
            .where(mine, Ticket.state == "done", Ticket.completed_at >= week)
            .order_by(Ticket.completed_at.desc())
            .limit(10)
        )
    ).all()
    open_rows = (
        await session.scalars(
            select(Ticket)
            .where(mine, Ticket.state == "open")
            .order_by(Ticket.due_by.asc().nulls_last())
            .limit(200)
        )
    ).all()
    has_po = exists().where(PurchaseRequest.ticket_id == Ticket.id, PurchaseRequest.status != "rejected")
    suggestions = (
        await session.scalars(
            select(Ticket).where(
                mine,
                Ticket.state == "open",
                Ticket.device_need.is_not(None),
                Ticket.po_dismissed.is_(False),
                ~has_po,
            )
        )
    ).all()
    return {
        "linked": True,
        "technician": me.name,
        "counts": {
            "pending": counts.get("pending", 0),
            "in_progress": counts.get("in_progress", 0),
            "on_hold": counts.get("on_hold", 0),
            "overdue": overdue or 0,
            "done_7d": len(done),
        },
        "open": [ticket_out(t, now) for t in open_rows],
        "done": [ticket_out(t, now) for t in done],
        "suggestions": [ticket_out(t, now) for t in suggestions],
    }
