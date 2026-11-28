"""Read SDP technicians and requests (Read credential) into local tables.

Incremental: requests updated since the newest stored update (1 h overlap); the first run reads
``lookback_days``. Technicians are linked to infra-ops users by e-mail unless linked by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor, record
from app.core.auth.models import User
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.sdp import SdpClient, SdpError
from app.core.logging import get_logger
from app.core.rbac.service import grant_role
from app.core.scheduler.service import get_task
from app.modules.workload.models import Technician, Ticket
from app.modules.workload.rules import device_need, normalize_ticket, ticket_phase, ticket_state
from app.modules.workload.settings import MODULE_ID, WorkloadSettings
from app.modules.workload.settings import load as load_module_settings

log = get_logger(__name__)
SYNC_TASK_KEY = "workload.sync"
OVERLAP_MS = 3_600_000
# shortcut: one sync reads at most this many requests; raise it if a backfill ever hits the cap.
MAX_ROWS = 50_000
PAGE_IDS = 100  # request ids per SDP search when re-reading categories
PAGE = 500  # tickets loaded per query when merging a sync


async def sync_technicians(
    session: AsyncSession,
    client: SdpClient,
    settings: WorkloadSettings | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> int:
    """Technicians from SDP's list, or (``rows`` given) only those seen on requests: then nobody is deactivated."""
    partial = rows is not None
    if rows is None:
        rows = await client.list_all("/api/v3/technicians", "technicians")
    now = datetime.now(UTC)
    existing = {t.id: t for t in (await session.scalars(select(Technician))).all()}
    seen: set[str] = set()
    for raw in rows:
        tid = str(raw.get("id") or "")
        if not tid:
            continue
        seen.add(tid)
        tech = existing.get(tid) or Technician(id=tid, mapped_manually=False)
        tech.name = str(raw.get("name") or "")[:256]
        tech.email = (raw.get("email_id") or None) and str(raw["email_id"]).lower()[:320]
        tech.active = True
        tech.synced_at = now
        session.add(tech)
        existing[tid] = tech
    for tid, tech in existing.items():
        if tid not in seen and not partial:
            tech.active = False
    await session.flush()
    await auto_link(session, list(existing.values()))
    return len(seen)


async def create_account(session: AsyncSession, tech: Technician, actor: Actor) -> User:
    """Give a technician an Operator account (SSO login by e-mail), confirmed by an administrator.
    Accounts are never created automatically: users are only the people an administrator adds or confirms."""
    email = (tech.email or "").lower()
    taken = await session.scalar(
        select(User).where((func.lower(User.upn) == email) | (func.lower(User.email) == email))
    )
    if taken is not None:  # never duplicate an account: link the existing one
        tech.user_id, tech.mapped_manually = taken.id, True
        return taken
    user = User(upn=email, display_name=tech.name or email, email=email)
    session.add(user)
    await session.flush()
    await grant_role(session, user.id, "operator")
    tech.user_id, tech.mapped_manually = user.id, True
    await record(
        session,
        actor=actor,
        module_id=MODULE_ID,
        action="workload.user_created",
        target_type="user",
        target_id=str(user.id),
        target_label=user.upn,
        after={"technician": tech.name, "role": "operator"},
    )
    await session.flush()
    return user


async def reclassify(session: AsyncSession, settings: WorkloadSettings, only_missing: bool = True) -> int:
    """Recompute state/phase/device need from stored status and subject (after a settings change or
    an upgrade): the incremental sync only re-reads tickets that changed on SDP."""
    stmt = select(Ticket)
    if only_missing:  # open tickets only: a rule change (e.g. a new "done" status name) closes them
        stmt = stmt.where(Ticket.state == "open")
    n = 0
    for t in (await session.scalars(stmt)).all():
        t.state = ticket_state(t.status, settings)
        t.phase = ticket_phase(t.status, t.state, settings)
        t.device_need = device_need(t.subject, settings)
        n += 1
    await session.flush()
    return n


async def auto_link(session: AsyncSession, techs: list[Technician]) -> None:
    """Link technicians to users whose e-mail or UPN equals the technician e-mail."""
    taken = {t.user_id for t in techs if t.user_id}
    for tech in techs:
        if tech.mapped_manually or tech.user_id or not tech.email:
            continue
        user = await session.scalar(
            select(User).where((func.lower(User.email) == tech.email) | (func.lower(User.upn) == tech.email))
        )
        if user is not None and user.id not in taken:
            tech.user_id = user.id
            taken.add(user.id)
    await session.flush()


async def sync_tickets(
    session: AsyncSession, client: SdpClient, settings: WorkloadSettings
) -> list[dict[str, Any]]:
    newest = await session.scalar(select(func.max(Ticket.sdp_updated_ms)))
    since = (
        newest - OVERLAP_MS
        if newest
        else int((datetime.now(UTC) - timedelta(days=settings.lookback_days)).timestamp() * 1000)
    )
    criteria: list[dict[str, Any]] = [
        {"field": "last_updated_time", "condition": "greater than", "value": str(since)}
    ]
    if settings.group_names:
        criteria.append(
            {
                "field": "group.name",
                "condition": "is",
                "values": settings.group_names,
                "logical_operator": "AND",
            }
        )
    rows = await client.list_all(
        "/api/v3/requests",
        "requests",
        search_criteria=criteria,
        sort_field="last_updated_time",
        max_rows=MAX_ROWS,
    )
    if len(rows) >= MAX_ROWS:
        log.warning("workload_sync_truncated", rows=len(rows))
    now = datetime.now(UTC)
    for start in range(0, len(rows), PAGE):
        page = [normalize_ticket(raw, settings) for raw in rows[start : start + PAGE]]
        ids = [d["id"] for d in page]
        known = {t.id: t for t in (await session.scalars(select(Ticket).where(Ticket.id.in_(ids)))).all()}
        for data in page:
            ticket = known.get(data["id"])
            if ticket is None:  # also catches an id repeated within the page
                ticket = known[data["id"]] = Ticket(id=data["id"])
            for k, v in data.items():
                setattr(ticket, k, v)
            ticket.synced_at = now
            session.add(ticket)
        await session.flush()
    await fill_categories(session, client, settings)
    return rows


async def fill_categories(session: AsyncSession, client: SdpClient, settings: WorkloadSettings) -> None:
    """Read open tickets stored before categories existed (category IS NULL) once more and fill them.

    A chunk SDP fails to answer keeps NULL and is retried by the next sync."""
    ids = list(
        await session.scalars(
            select(Ticket.id).where(Ticket.state == "open", Ticket.category.is_(None)).limit(MAX_ROWS)
        )
    )
    for start in range(0, len(ids), PAGE_IDS):
        chunk = ids[start : start + PAGE_IDS]
        try:
            rows = await client.list_all(
                "/api/v3/requests",
                "requests",
                search_criteria={"field": "id", "condition": "is", "values": chunk},
                sort_field="id",
                max_rows=len(chunk),
            )
        except SdpError as exc:
            log.warning("workload_category_reread_failed", error=str(exc))
            continue
        found = {str(r["id"]): normalize_ticket(r, settings) for r in rows if r.get("id") is not None}
        for ticket in (await session.scalars(select(Ticket).where(Ticket.id.in_(list(found))))).all():
            ticket.category, ticket.subcategory = (
                found[ticket.id]["category"],
                found[ticket.id]["subcategory"],
            )
        await session.flush()


async def run_sync(session: AsyncSession, requested_by: str | None = None) -> dict[str, Any]:
    settings = await load_module_settings(session)
    _, ctx = await open_connection(session, "sdp", Purpose.READ, profile_id=settings.sdp_profile_id)
    await reclassify(session, settings)
    async with SdpClient(ctx) as client:
        try:
            technicians: int | None = await sync_technicians(session, client, settings)
        except SdpError as exc:
            # Listing technicians needs an SDP admin permission the read key may lack: fall back to the
            # technicians assigned on the requests read below.
            log.warning("workload_technicians_unreadable", error=str(exc))
            technicians = None
        rows = await sync_tickets(session, client, settings)
        if technicians is None:
            seen = {str(t["id"]): t for r in rows if (t := r.get("technician") or {}).get("id")}
            technicians = await sync_technicians(session, client, settings, rows=list(seen.values()))
    stats = {"technicians": technicians, "tickets": len(rows)}
    await record(
        session,
        actor=Actor(None, requested_by or "system"),
        module_id=MODULE_ID,
        action="workload.sync",
        system_type="sdp",
        after=stats,
    )
    await session.commit()
    log.info("workload_sync_done", **stats)
    return stats


async def scheduled_sync(session: AsyncSession) -> dict[str, Any]:
    task = await get_task(session, SYNC_TASK_KEY)
    return await run_sync(session, task.requested_by if task else None)
