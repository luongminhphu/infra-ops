"""Helpdesk/purchase items for the "Việc tiếp theo" queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workload.api import _my_technician
from app.modules.workload.models import PurchaseEvidence, PurchaseRequest, Ticket

MAX_TICKETS = 15


def _days(delta: timedelta) -> int:
    return max(1, int(delta.total_seconds() // 86400))


async def load(session: AsyncSession, user: Any) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    items: list[dict[str, Any]] = []
    me = await _my_technician(session, user)
    if me is not None:
        # Overdue first, then due within a day; tasks are worked on in ServiceDesk Plus.
        due = (
            await session.scalars(
                select(Ticket)
                .where(
                    Ticket.technician_id == me.id,
                    Ticket.state == "open",
                    Ticket.due_by < now + timedelta(days=1),
                )
                .order_by(Ticket.due_by)
                .limit(MAX_TICKETS)
            )
        ).all()
        for t in due:
            late = t.due_by < now  # type: ignore[operator]
            items.append(
                {
                    "key": f"ticket:{t.id}",
                    "kind": "ticket",
                    "priority": 1 if late else 2,
                    "title": f"Request #{t.display_id or t.id} {'trễ ' + str(_days(now - t.due_by)) + ' ngày' if late else 'đến hạn trong hôm nay'}",  # type: ignore[operator]
                    "detail": t.subject,
                    "due": t.due_by,
                    "href": f"/workload/tickets?state={'overdue' if late else 'open'}",
                    "action": "Xem task",
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
                "title": f"Bổ sung bằng chứng Function Head cho “{p.item}”",
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
    return items
