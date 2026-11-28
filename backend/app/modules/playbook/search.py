"""Global search: guides, with the visibility of the guide list (readers: published only)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser
from app.core.modules.manifest import SearchSource
from app.core.text import fold, fold_sql
from app.modules.playbook.api import EDIT, VIEW
from app.modules.playbook.models import Guide


async def search_guides(session: AsyncSession, user: CurrentUser, q: str, limit: int) -> list[dict[str, Any]]:
    hay = func.concat_ws(" ", Guide.title, Guide.category, Guide.description)
    stmt = select(Guide).where(fold_sql(hay).contains(fold(q), autoescape=True))
    if not user.has(EDIT):
        stmt = stmt.where(Guide.published_version.is_not(None))
    rows = await session.scalars(stmt.order_by(func.lower(Guide.title)).limit(limit))
    return [
        {
            "title": g.title,
            "detail": g.category or ("Bản nháp" if g.published_version is None else "Hướng dẫn"),
            "href": f"/playbook/{g.id}" + ("/edit" if g.published_version is None else ""),
            "kind": "guide",
        }
        for g in rows
    ]


SEARCH = SearchSource("playbook.guides", VIEW, "Playbook", search_guides)
