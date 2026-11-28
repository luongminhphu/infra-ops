"""Global search: tasks (tickets) and purchase proposals, with the row scope and fields of their lists."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser
from app.core.modules.manifest import SearchSource
from app.core.text import fold, fold_sql
from app.core.timeutil import fmt_date
from app.modules.workload.api import IN_APP, NO_TECHNICIAN, _scope
from app.modules.workload.models import PurchaseRequest, Ticket
from app.modules.workload.purchases_api import _scoped

PURCHASE_STATUS = {
    "proposed": "Chờ duyệt",
    "approved": "Đã duyệt",
    "ordered": "Đã đặt hàng",
    "received": "Đã nhận",
    "rejected": "Từ chối",
}


async def search_tickets(
    session: AsyncSession, user: CurrentUser, q: str, limit: int
) -> list[dict[str, Any]]:
    # Fields the task list matches (never the description); operators without view_all only their own.
    technician_id = await _scope(session, user, "")
    hay = func.concat_ws(" ", Ticket.display_id, Ticket.subject, Ticket.requester_name)
    stmt = select(Ticket).where(IN_APP, fold_sql(hay).contains(fold(q), autoescape=True))
    if technician_id == NO_TECHNICIAN:
        return []
    if technician_id:
        stmt = stmt.where(Ticket.technician_id == technician_id)
    rows = await session.scalars(stmt.order_by(Ticket.created_at.desc()).limit(limit))
    return [
        {
            "title": f"#{t.display_id or t.id} {t.subject}",
            "detail": " · ".join(x for x in (t.requester_name, t.status, fmt_date(t.created_at)) if x),
            "href": f"/workload/tickets?state=all&q={t.display_id or t.id}",  # closed tickets too
            "kind": "ticket",
        }
        for t in rows
    ]


async def search_purchases(
    session: AsyncSession, user: CurrentUser, q: str, limit: int
) -> list[dict[str, Any]]:
    # Item, employee and request number only: reasons, notes and prices are not matched.
    hay = func.concat_ws(
        " ", PurchaseRequest.item, PurchaseRequest.employee, PurchaseRequest.ticket_display_id
    )
    rows = await session.scalars(
        _scoped(user)
        .where(fold_sql(hay).contains(fold(q), autoescape=True))
        .order_by(PurchaseRequest.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "title": p.item,
            "detail": " · ".join(
                x for x in (p.employee, PURCHASE_STATUS.get(p.status, p.status), fmt_date(p.created_at)) if x
            ),
            "href": f"/workload/purchases?open={p.id}",
            "kind": "purchase",
        }
        for p in rows
    ]


SEARCH = [
    SearchSource("workload.tickets", "workload.tickets.view", "Task", search_tickets),
    SearchSource("workload.purchases", "workload.tickets.view", "Đề xuất mua hàng", search_purchases),
]
