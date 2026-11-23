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
from app.core.connectors.sdp import SdpClient
from app.core.logging import get_logger
from app.core.rbac.service import grant_role
from app.core.scheduler.service import get_task
from app.core.settings.service import load_settings
from app.modules.workload.models import Technician, Ticket
from app.modules.workload.rules import device_need, normalize_ticket, ticket_phase, ticket_state
from app.modules.workload.settings import MODULE_ID, SETTINGS_KEY, WorkloadSettings

log = get_logger(__name__)
SYNC_TASK_KEY = "workload.sync"
OVERLAP_MS = 3_600_000
# shortcut: one sync reads at most this many requests; raise it if a backfill ever hits the cap.
MAX_ROWS = 50_000


async def sync_technicians(
    session: AsyncSession, client: SdpClient, settings: WorkloadSettings | None = None
) -> int:
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
        if tid not in seen:
            tech.active = False
    await session.flush()
    await auto_link(session, list(existing.values()))
    if settings is None or settings.auto_create_users:
        await create_accounts(session, [t for t in existing.values() if t.active])
    return len(seen)


async def create_accounts(session: AsyncSession, techs: list[Technician]) -> int:
    """Give every active technician without a linked user an Operator account (SSO login by e-mail).

    A link cleared by hand (mapped_manually without user) is left alone.
    """
    created = 0
    for tech in techs:
        if tech.user_id or tech.mapped_manually or not tech.email:
            continue
        # An account with this e-mail that auto_link did not use (linked elsewhere): never duplicate it.
        taken = await session.scalar(
            select(User.id).where(
                (func.lower(User.upn) == tech.email) | (func.lower(User.email) == tech.email)
            )
        )
        if taken is not None:
            continue
        user = User(upn=tech.email, display_name=tech.name or tech.email, email=tech.email)
        session.add(user)
        await session.flush()
        await grant_role(session, user.id, "operator")
        tech.user_id = user.id
        created += 1
        await record(
            session,
            actor=Actor(None, "system"),
            module_id=MODULE_ID,
            action="workload.user_created",
            target_type="user",
            target_id=str(user.id),
            target_label=user.upn,
            after={"technician": tech.name, "role": "operator"},
        )
    await session.flush()
    return created


async def reclassify(session: AsyncSession, settings: WorkloadSettings, only_missing: bool = True) -> int:
    """Recompute state/phase/device need from stored status and subject (after a settings change or
    an upgrade): the incremental sync only re-reads tickets that changed on SDP."""
    stmt = select(Ticket)
    if only_missing:
        stmt = stmt.where(Ticket.state == "open", Ticket.phase.is_(None))
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


async def sync_tickets(session: AsyncSession, client: SdpClient, settings: WorkloadSettings) -> int:
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
    for raw in rows:
        data = normalize_ticket(raw, settings)
        ticket = await session.get(Ticket, data["id"]) or Ticket(id=data["id"])
        for k, v in data.items():
            setattr(ticket, k, v)
        ticket.synced_at = now
        session.add(ticket)
    await session.flush()
    return len(rows)


async def run_sync(session: AsyncSession, requested_by: str | None = None) -> dict[str, Any]:
    settings = await load_settings(session, MODULE_ID, SETTINGS_KEY, WorkloadSettings)
    _, ctx = await open_connection(session, "sdp", Purpose.READ, profile_id=settings.sdp_profile_id)
    await reclassify(session, settings)
    async with SdpClient(ctx) as client:
        technicians = await sync_technicians(session, client, settings)
        tickets = await sync_tickets(session, client, settings)
    stats = {"technicians": technicians, "tickets": tickets}
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
