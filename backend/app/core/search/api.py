from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser, require
from app.core.db import get_session
from app.core.logging import get_logger
from app.core.modules.registry import get_registry
from app.core.permissions import DASHBOARD_VIEW
from app.core.text import fold

router = APIRouter(tags=["search"])
log = get_logger(__name__)

MIN_CHARS = 2
PER_SOURCE = 5
SOURCE_TIMEOUT = 2.0


@router.get("/search")
async def search(
    q: str = Query("", max_length=100),
    user: CurrentUser = Depends(require(DASHBOARD_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Records matching `q` from every source the user may use; a slow or failing source is only reported."""
    q = q.strip()
    groups: list[dict[str, Any]] = []
    failed: list[str] = []
    if len(fold(q)) < MIN_CHARS:
        return {"groups": groups, "failed": failed}
    # Sequential: one AsyncSession is not safe for concurrent use.
    for m in get_registry().modules:
        for src in m.search_sources:
            if not user.has(src.permission):
                continue
            try:
                items = await asyncio.wait_for(src.search(session, user, q, PER_SOURCE), SOURCE_TIMEOUT)
            except Exception:
                log.exception("search_source_failed", source=src.key)
                await session.rollback()  # a failed query leaves the transaction unusable for the next source
                if m.name not in failed:
                    failed.append(m.name)
                continue
            if items:
                groups.append({"key": src.key, "label": src.label, "items": items[:PER_SOURCE]})
    return {"groups": groups, "failed": failed}
