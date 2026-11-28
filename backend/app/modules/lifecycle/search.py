"""Global search: RTR requests read from SDP (same permission and fields as the requests list)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser
from app.core.modules.manifest import SearchSource
from app.core.text import fold, fold_sql
from app.core.timeutil import fmt_date
from app.modules.lifecycle.models import LifecycleRequest


async def search_requests(
    session: AsyncSession, user: CurrentUser, q: str, limit: int
) -> list[dict[str, Any]]:
    hay = func.concat_ws(
        " ", LifecycleRequest.display_id, LifecycleRequest.employee_id, LifecycleRequest.full_name,
        LifecycleRequest.subject,
    )  # fmt: skip
    rows = await session.scalars(
        select(LifecycleRequest)
        .where(fold_sql(hay).contains(fold(q), autoescape=True))
        .order_by(LifecycleRequest.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "title": f"#{r.display_id or r.id} {r.subject}",
            "detail": " · ".join(
                x for x in (r.employee_id, r.full_name, r.status, fmt_date(r.created_at)) if x
            ),
            "href": f"/lifecycle/requests?open={r.id}",
            "kind": "request",
        }
        for r in rows
    ]


SEARCH = SearchSource("lifecycle.requests", "lifecycle.reconciliation.view", "Request SDP", search_requests)
