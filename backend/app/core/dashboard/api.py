from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser, require
from app.core.db import get_session
from app.core.logging import get_logger
from app.core.modules.registry import get_registry
from app.core.permissions import DASHBOARD_VIEW

router = APIRouter(tags=["dashboard"])
log = get_logger(__name__)


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser = Depends(require(DASHBOARD_VIEW)), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    """Widgets registered by every enabled module, filtered by the viewer's permissions.

    A failing widget returns an error state instead of failing the whole dashboard.
    """
    widgets = sorted(
        (w for m in get_registry().modules for w in m.dashboard_widgets if user.has(w.permission)),
        key=lambda w: w.order,
    )
    out: list[dict[str, Any]] = []
    for w in widgets:
        try:
            data: dict[str, Any] | None = await w.load(session)
            error = None
        except Exception:
            log.exception("dashboard_widget_failed", widget=w.key)
            data, error = None, "Không tải được dữ liệu."
        out.append({"key": w.key, "title": w.title, "kind": w.kind, "data": data, "error": error})
    return out


@router.get("/me/next")
async def next_actions(
    user: CurrentUser = Depends(require(DASHBOARD_VIEW)), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """The signed-in person's queue across modules: most urgent first, then by due date."""
    items: list[dict[str, Any]] = []
    failed: list[str] = []
    for m in get_registry().modules:
        for src in m.next_actions:
            if not user.has(src.permission):
                continue
            try:
                items.extend(await src.load(session, user))
            except Exception:
                log.exception("next_action_source_failed", source=src.key)
                failed.append(m.name)
    items.sort(key=lambda i: (i["priority"], i.get("due") is None, str(i.get("due") or "")))
    return {"items": items, "failed": failed}
