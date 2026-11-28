"""The "Việc tiếp theo" queue of one person, shared by ``/me/next`` and the scheduled digest."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser
from app.core.logging import get_logger
from app.core.modules.registry import get_registry

log = get_logger(__name__)


async def collect_next_actions(
    session: AsyncSession, user: CurrentUser
) -> tuple[list[dict[str, Any]], list[str]]:
    """Items from every source the user may see (most urgent first) and the names of modules that failed."""
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
    items = list({i["key"]: i for i in items}.values())  # one row per key (e.g. "enter your API key")
    items.sort(key=lambda i: (i["priority"], i.get("due") is None, str(i.get("due") or "")))
    return items, failed
