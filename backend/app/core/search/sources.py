"""Search sources owned by core. Row scope mirrors the list endpoints (`GET /jobs`, `GET /documents`)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import SearchSource

# Imports stay inside the functions to avoid an import cycle within core (core.permissions imports this file).


async def search_jobs(session: AsyncSession, user: Any, q: str, limit: int) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from app.core.modules.registry import get_registry
    from app.core.text import fold, fold_sql
    from app.core.timeutil import fmt_date
    from app.core.workflow.models import Job

    allowed = {w.key: w for _, w in get_registry().workflows() if user.has(w.view_permission)}
    if not allowed:
        return []
    # Workflows whose other people's jobs the user may not see: mine and the unassigned ones only.
    scoped = [k for k, w in allowed.items() if w.view_all_permission and not user.has(w.view_all_permission)]
    visible = or_(
        Job.workflow_key.notin_(scoped) if scoped else true(),
        Job.assignee_user_id == user.id,
        Job.assignee_user_id.is_(None),
    )
    hay = func.concat_ws(" ", Job.subject_label, Job.subject_key, Job.external_ref)
    rows = await session.scalars(
        select(Job)
        .where(Job.workflow_key.in_(allowed), visible, fold_sql(hay).contains(fold(q), autoescape=True))
        .order_by(Job.updated_at.desc())
        .limit(limit)
    )
    return [
        {
            "title": j.subject_label or j.subject_key,
            "detail": f"{allowed[j.workflow_key].title} · {fmt_date(j.updated_at)}",
            "href": f"/lifecycle/jobs/{j.id}",
            "kind": "job",
        }
        for j in rows
    ]


async def search_documents(session: AsyncSession, user: Any, q: str, limit: int) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from app.core.documents import service
    from app.core.documents.models import Document
    from app.core.modules.registry import get_registry
    from app.core.text import fold, fold_sql
    from app.core.timeutil import fmt_date

    kinds = {k.key: k for _, k in get_registry().document_kinds() if user.has(k.view_permission)}
    if not kinds:
        return []
    scoped = [
        or_(Document.kind != kd.key, c)
        for kd in kinds.values()
        if (c := service.scope_clause(user, kd)) is not None
    ]
    hay = func.concat_ws(" ", Document.number, Document.subject_label, Document.subject_key)
    rows = await session.scalars(
        select(Document)
        .where(Document.kind.in_(kinds), *scoped, fold_sql(hay).contains(fold(q), autoescape=True))
        .order_by(Document.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "title": d.number or d.subject_label or kinds[d.kind].title,
            "detail": f"{kinds[d.kind].title} · {d.subject_label} · {fmt_date(d.created_at)}",
            "href": f"/lifecycle/documents/{d.id}",
            "kind": "document",
        }
        for d in rows
    ]


SEARCH_SOURCES = [
    SearchSource("core.jobs", "core.dashboard.view", "Job", search_jobs),
    SearchSource("core.documents", "core.dashboard.view", "Biên bản", search_documents),
]
