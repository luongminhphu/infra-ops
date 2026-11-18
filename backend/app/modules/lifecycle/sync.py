"""Read SDP and BioStar with Read credentials, store snapshots and reconcile.

Each source is read independently: a failure in one system marks the run "partial" and the
rules that depend on that system are skipped instead of producing false discrepancies.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor, record
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.biostar import BioStarClient
from app.core.connectors.http import ConnectorHttpError
from app.core.connectors.sdp import SdpClient
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.scheduler.service import get_task
from app.core.settings.service import load_settings
from app.modules.lifecycle import normalize
from app.modules.lifecycle.models import IssueAck, LifecycleRequest, ReconRow, Snapshot, SyncRun
from app.modules.lifecycle.parsing import parse_rtr_description
from app.modules.lifecycle.reconcile import reconcile
from app.modules.lifecycle.settings import MODULE_ID, SETTINGS_KEY, LifecycleSettings

log = get_logger(__name__)
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SYNC_TASK_KEY = "lifecycle.sync"


def _err(exc: Exception) -> str:
    if isinstance(exc, ConnectorHttpError):
        return f"{exc.message} {exc.hint}".strip()
    if isinstance(exc, AppError):
        return exc.message
    return f"{type(exc).__name__}: {exc}"


def _ms_to_dt(value: Any) -> datetime | None:
    raw = value.get("value") if isinstance(value, dict) else None
    return datetime.fromtimestamp(int(raw) / 1000, UTC) if raw and str(raw).isdigit() else None


def apply_parse(req: LifecycleRequest, description_html: str, settings: LifecycleSettings) -> None:
    parsed = parse_rtr_description(description_html, settings)
    req.employee_id = parsed.employee_id
    req.full_name = parsed.full_name
    req.change_type_raw = parsed.change_type_raw
    req.kind = parsed.kind
    req.effective_date = parsed.effective_date
    req.fields = {
        **parsed.fields,
        "manager_name": parsed.manager_name,
        "manager_employee_id": parsed.manager_employee_id,
    }
    req.parse_errors = parsed.errors


async def sync_requests(
    session: AsyncSession, client: SdpClient, settings: LifecycleSettings
) -> dict[str, int]:
    since = datetime.now(UTC) - timedelta(days=settings.rtr_lookback_days)
    criteria = [
        {"field": "requester.name", "condition": "is", "value": settings.rtr_requester_name},
        {
            "field": "created_time",
            "condition": "greater than",
            "value": str(int(since.timestamp() * 1000)),
            "logical_operator": "AND",
        },
    ]
    rows = await client.list_all(
        "/api/v3/requests", "requests", search_criteria=criteria, sort_field="created_time"
    )
    existing = {r.id: r for r in (await session.scalars(select(LifecycleRequest))).all()}
    sem = asyncio.Semaphore(4)
    fetched = 0

    async def detail(rid: str) -> dict[str, Any]:
        async with sem:
            return (await client.get(f"/api/v3/requests/{rid}")).get("request") or {}

    to_fetch: list[dict[str, Any]] = []
    for raw in rows:
        rid = str(raw.get("id"))
        updated = str((raw.get("last_updated_time") or {}).get("value") or "")
        current = existing.get(rid)
        if current is None or current.sdp_updated_ms != updated or not current.description_text:
            to_fetch.append(raw)
    details = await asyncio.gather(*(detail(str(r["id"])) for r in to_fetch))
    for raw, full in zip(to_fetch, details, strict=True):
        rid = str(raw["id"])
        req = existing.get(rid) or LifecycleRequest(id=rid)
        if rid not in existing:
            session.add(req)
            existing[rid] = req
        req.display_id = str(raw.get("display_id") or "") or None
        req.subject = str(raw.get("subject") or "")[:500]
        req.status = (raw.get("status") or {}).get("name")
        req.created_at = _ms_to_dt(raw.get("created_time"))
        req.sdp_updated_ms = str((raw.get("last_updated_time") or {}).get("value") or "")
        description = full.get("description") or ""
        req.description_text = description
        apply_parse(req, description, settings)
        req.last_synced_at = datetime.now(UTC)
        fetched += 1
    await session.flush()
    return {"requests_listed": len(rows), "requests_fetched": fetched}


async def reparse_all(session: AsyncSession, settings: LifecycleSettings) -> int:
    """Re-apply the parser after label/mapping changes, without calling SDP."""
    reqs = (await session.scalars(select(LifecycleRequest))).all()
    for r in reqs:
        apply_parse(r, r.description_text, settings)
    await session.flush()
    return len(reqs)


def request_dict(r: LifecycleRequest) -> dict[str, Any]:
    return {
        "id": r.id,
        "display_id": r.display_id,
        "subject": r.subject,
        "employee_id": r.employee_id,
        "full_name": r.full_name,
        "kind": r.kind,
        "effective_date": r.effective_date.isoformat() if r.effective_date else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "department": (r.fields or {}).get("department"),
        "parse_errors": r.parse_errors,
    }


async def run_sync(
    session: AsyncSession,
    *,
    trigger: str = "scheduled",
    requested_by: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    settings = await load_settings(session, MODULE_ID, SETTINGS_KEY, LifecycleSettings)
    run = SyncRun(trigger=trigger, requested_by=requested_by, status="running", stats={}, errors={}, notes=[])
    session.add(run)
    await session.commit()

    sources = {"sdp": False, "assets": False, "biostar": False}
    errors: dict[str, str] = {}
    stats: dict[str, Any] = {}
    sdp_users: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    bs_users: list[dict[str, Any]] = []

    try:
        _, ctx = await open_connection(session, "sdp", Purpose.READ, profile_id=settings.sdp_profile_id)
        async with SdpClient(ctx) as client:
            try:
                stats.update(await sync_requests(session, client, settings))
            except Exception as exc:
                errors["requests"] = _err(exc)
            try:
                raw_users = await client.list_all("/api/v3/users", "users")
                sdp_users = [normalize.sdp_user(u, settings.sdp_user_employee_id_field) for u in raw_users]
                sources["sdp"] = "requests" not in errors
            except Exception as exc:
                errors["sdp_users"] = _err(exc)
            try:
                raw_assets = await client.list_all("/api/v3/assets", "assets")
                types = {t.lower() for t in settings.asset_product_types}
                assets = [
                    a
                    for a in map(normalize.sdp_asset, raw_assets)
                    if not types or (a.get("product_type") or "").lower() in types
                ]
                sources["assets"] = "sdp_users" not in errors
            except Exception as exc:
                errors["sdp_assets"] = _err(exc)
    except Exception as exc:
        errors["sdp"] = _err(exc)

    try:
        _, ctx = await open_connection(
            session, "biostar", Purpose.READ, profile_id=settings.biostar_profile_id
        )
        async with BioStarClient(ctx) as client:
            bs_users = [normalize.biostar_user(u) for u in await client.list_users()]
        sources["biostar"] = True
    except Exception as exc:
        errors["biostar"] = _err(exc)

    for system, items, key in (
        ("sdp_user", sdp_users, "id"),
        ("sdp_asset", assets, "id"),
        ("biostar_user", bs_users, "user_id"),
    ):
        session.add_all(
            Snapshot(
                run_id=run.id,
                system=system,
                external_id=str(i[key]),
                employee_id=i.get("employee_id") or (i["user_id"] if system == "biostar_user" else None),
                data=i,
            )
            for i in items
        )

    requests = [request_dict(r) for r in (await session.scalars(select(LifecycleRequest))).all()]
    result = reconcile(
        requests, sdp_users, assets, bs_users, settings, today or datetime.now(TZ).date(), sources
    )
    acks = {(a.employee_id, a.rule) for a in (await session.scalars(select(IssueAck))).all()}
    issue_total = 0
    for row in result.rows:
        data = row.to_dict()
        for issue in data["issues"]:
            issue["acked"] = (row.employee_id, issue["rule"]) in acks
        open_issues = [i for i in data["issues"] if not i["acked"]]
        issue_total += len(open_issues)
        session.add(
            ReconRow(
                run_id=run.id,
                employee_id=row.employee_id,
                full_name=row.full_name[:256],
                department=row.department,
                kind=row.kind,
                max_severity=max(
                    (i["severity"] for i in open_issues),
                    key={"high": 3, "medium": 2, "low": 1}.__getitem__,
                    default=None,
                ),
                issue_count=len(open_issues),
                data=data,
            )
        )

    stats.update(
        {
            "requests": len(requests),
            "sdp_users": len(sdp_users),
            "assets": len(assets),
            "biostar_users": len(bs_users),
            "employees": len(result.rows),
            "issues": issue_total,
        }
    )
    if sources["sdp"]:
        # One draft job per onboard/offboard request (drafts never write to external systems).
        from app.modules.lifecycle.jobs_api import auto_create_jobs

        stats["jobs_created"] = await auto_create_jobs(session)
    run.stats = stats
    run.errors = errors
    run.notes = result.notes
    run.finished_at = datetime.now(UTC)
    run.status = (
        "success"
        if all(sources.values()) and not errors
        else ("failure" if not any(sources.values()) else "partial")
    )

    # Retention: drop old snapshot details, keep run summaries for trend/history.
    cutoff = datetime.now(UTC) - timedelta(days=settings.snapshot_retention_days)
    old_runs = select(SyncRun.id).where(SyncRun.started_at < cutoff)
    await session.execute(delete(Snapshot).where(Snapshot.run_id.in_(old_runs)))
    await session.execute(delete(ReconRow).where(ReconRow.run_id.in_(old_runs)))

    await record(
        session,
        actor=Actor(None, requested_by or "system"),
        module_id=MODULE_ID,
        action="lifecycle.sync",
        result="success" if run.status != "failure" else "failure",
        target_type="sync_run",
        target_id=str(run.id),
        after={"status": run.status, **stats},
        message="; ".join(errors.values())[:1000] or None,
    )
    await session.commit()
    log.info("lifecycle_sync_done", status=run.status, **stats)
    return {"run_id": str(run.id), "status": run.status, **stats}


async def scheduled_sync(session: AsyncSession) -> dict[str, Any]:
    """Scheduler entry point; a manual "Sync now" sets requested_by on the task row."""
    task = await get_task(session, SYNC_TASK_KEY)
    requested_by = task.requested_by if task else None
    return await run_sync(
        session, trigger="manual" if requested_by else "scheduled", requested_by=requested_by
    )
