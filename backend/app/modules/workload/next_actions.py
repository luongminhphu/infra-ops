"""Helpdesk/purchase items for the "Việc tiếp theo" queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.connections.service import personal_key_item
from app.core.settings.service import load_settings
from app.modules.workload.api import _my_technician
from app.modules.workload.models import PurchaseEvidence, PurchaseRequest, Ticket
from app.modules.workload.settings import MODULE_ID, SETTINGS_KEY, WorkloadSettings


def _days(delta: timedelta) -> int:
    return max(1, int(delta.total_seconds() // 86400))


async def load(session: AsyncSession, user: Any) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    items: list[dict[str, Any]] = []
    me = await _my_technician(session, user)
    if user.has("workload.purchase.receive") or user.has("workload.tickets.assign"):
        settings = await load_settings(session, MODULE_ID, SETTINGS_KEY, WorkloadSettings)
        if key := await personal_key_item(session, user.id, "sdp", settings.sdp_profile_id):
            items.append(key)  # same key as the lifecycle one: the queue shows it once
    if me is None and not user.has("workload.tickets.view_all"):
        # Without the link the person's SDP tasks are silently missing: say so first.
        manager = user.has("workload.settings.manage")
        items.append(
            {
                "key": "tickets:unlinked",
                "kind": "notice",
                "priority": 0,
                "passive": not manager,
                "title": "Tài khoản chưa liên kết với technician trên ServiceDesk Plus",
                "detail": "Task SDP của bạn chưa hiện ở đây. "
                + (
                    "Liên kết ở Cấu hình công việc."
                    if manager
                    else "Nhờ Manager liên kết ở Cấu hình công việc (theo e-mail technician trên SDP)."
                ),
                "due": None,
                "href": "/workload/settings" if manager else "/workload/tickets",
                "action": "Liên kết" if manager else "Xem task",
            }
        )
    if me is not None:
        # One item per group (overdue / due today): tasks are worked on in ServiceDesk Plus, so one
        # reminder is enough and the person's process work (jobs, with a due date) ranks above it.
        due = (
            await session.scalars(
                select(Ticket)
                .where(
                    Ticket.technician_id == me.id,
                    Ticket.state == "open",
                    Ticket.due_by < now + timedelta(days=1),
                )
                .order_by(Ticket.due_by)
            )
        ).all()
        late = [t for t in due if t.due_by < now]  # type: ignore[operator]
        today = [t for t in due if t.due_by >= now]  # type: ignore[operator]
        for group, prio, state, title in (
            (late, 1, "overdue", "quá hạn"),
            (today, 2, "open", "đến hạn trong hôm nay"),
        ):
            if not group:
                continue
            first = group[0]
            ref = f"#{first.display_id or first.id}"
            if len(group) == 1:
                head = f"Request {ref} {title}" + (
                    f" ({_days(now - first.due_by)} ngày)" if prio == 1 else ""
                )  # type: ignore[operator]
                detail = first.subject
            else:
                head = f"{len(group)} request {title}"
                detail = f"Lâu nhất: {ref} – {first.subject}"
            items.append(
                {
                    "key": f"tickets:{state}",
                    "kind": "ticket",
                    "priority": prio,
                    "title": head,
                    "detail": detail,
                    "due": None,
                    "href": f"/workload/tickets?tech=me&state={state}",
                    "action": "Xem task trên danh sách",
                }
            )
        has_po = exists().where(PurchaseRequest.ticket_id == Ticket.id, PurchaseRequest.status != "rejected")
        for t in (
            await session.scalars(
                select(Ticket)
                .where(
                    Ticket.technician_id == me.id,
                    Ticket.state == "open",
                    Ticket.device_need.is_not(None),
                    Ticket.po_dismissed.is_(False),
                    ~has_po,
                )
                .limit(10)
            )
        ).all():
            q = {"ticket": t.id, "ref": t.display_id or t.id, "kind": t.device_need, "item": t.subject[:300],
                 "employee": t.requester_name or "", "reason": f"Request #{t.display_id or t.id}: {t.subject}"}  # fmt: skip
            items.append(
                {
                    "key": f"suggest:{t.id}",
                    "kind": "purchase_suggestion",
                    "priority": 4,
                    "title": f"Request #{t.display_id or t.id} có thể cần mua thiết bị",
                    "detail": t.subject,
                    "due": None,
                    "href": f"/workload/purchases/new?{urlencode(q)}",
                    "action": "Lập đề xuất",
                }
            )
    # My proposals that cannot be approved yet: no Function Head evidence.
    no_ev = ~exists().where(PurchaseEvidence.purchase_id == PurchaseRequest.id)
    for p in (
        await session.scalars(
            select(PurchaseRequest).where(
                PurchaseRequest.created_by == user.upn, PurchaseRequest.status == "proposed", no_ev
            )
        )
    ).all():
        items.append(
            {
                "key": f"evidence:{p.id}",
                "kind": "purchase_evidence",
                "priority": 2,
                "title": f"Bổ sung bằng chứng Function Head: {p.item}",
                "detail": "Chưa có bằng chứng thì Supervisor chưa duyệt được.",
                "due": None,
                "href": f"/workload/purchases?open={p.id}",
                "action": "Bổ sung",
            }
        )
    # Supervisors: proposals with evidence waiting for a decision (one item for the batch).
    if user.has("workload.purchase.manage"):
        waiting = await session.scalar(
            select(func.count()).where(PurchaseRequest.status == "proposed", ~no_ev)
        )
        if waiting:
            items.append(
                {
                    "key": "approve:proposed",
                    "kind": "purchase_approval",
                    "priority": 1,
                    "title": f"{waiting} đề xuất mua đang chờ duyệt",
                    "detail": "Đã có bằng chứng Function Head – duyệt từng cái hoặc hàng loạt.",
                    "due": None,
                    "href": "/workload/purchases?status=proposed",
                    "action": "Duyệt",
                }
            )
    # Ordered goods not received yet: receive them PO by PO when they arrive.
    ordered = 0
    if user.has("workload.purchase.receive"):
        ordered = await session.scalar(select(func.count()).where(PurchaseRequest.status == "ordered"))
    if ordered:
        items.append(
            {
                "key": "receive:ordered",
                "kind": "purchase_receive",
                "priority": 3,
                "title": f"{ordered} hạng mục đã đặt mua chờ nhận hàng",
                "detail": "Khi hàng về: quét serial, hệ thống nhập kho SDP.",
                "due": None,
                "href": "/workload/purchases/receive",
                "action": "Nhận hàng",
            }
        )
    return items


async def summary(session: AsyncSession, user: Any) -> list[dict[str, Any]]:
    """My SDP tasks: done in the last 7 days, overdue, due within 3 days, open."""
    me = await _my_technician(session, user)
    if me is None:
        return []
    now = datetime.now(UTC)
    mine = Ticket.technician_id == me.id
    is_open = Ticket.state == "open"

    async def count(*where: Any) -> int:
        return int(await session.scalar(select(func.count()).select_from(Ticket).where(mine, *where)) or 0)

    return [
        {"key": "tickets:done", "group": "done", "label": "task SDP", "href": "/workload/tickets?tech=me&state=done",
         "count": await count(Ticket.state == "done", Ticket.completed_at >= now - timedelta(days=7))},
        {"key": "tickets:late", "group": "late", "label": "task SDP", "href": "/workload/tickets?tech=me&state=overdue",
         "count": await count(is_open, Ticket.due_by < now)},
        {"key": "tickets:soon", "group": "soon", "label": "task SDP", "href": "/workload/tickets?tech=me&state=open",
         "count": await count(is_open, Ticket.due_by >= now, Ticket.due_by < now + timedelta(days=3))},
        {"key": "tickets:open", "group": "open", "label": "task SDP", "href": "/workload/tickets?tech=me&state=open",
         "count": await count(is_open)},
    ]  # fmt: skip
