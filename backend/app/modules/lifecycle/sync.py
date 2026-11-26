"""Read SDP and BioStar with Read credentials, store snapshots and reconcile.

Each source is read independently: a failure in one system marks the run "partial" and the
rules that depend on that system are skipped instead of producing false discrepancies.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

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
from app.core.timeutil import today_vn
from app.modules.lifecycle import normalize
from app.modules.lifecycle.models import AssetReturn, IssueAck, LifecycleRequest, ReconRow, Snapshot, SyncRun
from app.modules.lifecycle.parsing import parse_rtr_description
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.reconcile import reconcile
from app.modules.lifecycle.settings import MODULE_ID, LifecycleSettings, main_biostar
from app.modules.lifecycle.settings import load as load_module_settings

log = get_logger(__name__)
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


def set_technician(req: LifecycleRequest, raw: dict[str, Any]) -> None:
    tech = raw.get("technician") or {}
    req.technician_id = str(tech["id"]) if tech.get("id") else None
    req.technician_name = (tech.get("name") or None) and str(tech["name"])[:256]
    req.technician_email = (tech.get("email_id") or None) and str(tech["email_id"]).lower()[:320]


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
    # The technician comes with the list rows: keep it current for every request (cheap, no detail call).
    for raw in rows:
        known = existing.get(str(raw.get("id")))
        if known is not None:
            set_technician(known, raw)
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
        set_technician(req, {**raw, **{k: v for k, v in full.items() if k == "technician" and v}})
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


async def track_returns(
    session: AsyncSession, assets: list[dict[str, Any]], settings: LifecycleSettings
) -> int:
    """Mark assets back In Store that the previous sync saw assigned (thu hồi); clear marks of re-assigned ones."""
    prev_run = await latest_run(session)
    prev = (
        {
            s.external_id: s.data
            for s in (
                await session.scalars(
                    select(Snapshot).where(Snapshot.run_id == prev_run.id, Snapshot.system == "sdp_asset")
                )
            ).all()
        }
        if prev_run
        else {}
    )
    marks = {m.asset_id: m for m in (await session.scalars(select(AssetReturn))).all()}
    added = 0
    for a in assets:
        aid = a["id"]
        if a.get("user_id"):
            if aid in marks:  # assigned again: no longer returned stock
                await session.delete(marks[aid])
            continue
        before = prev.get(aid) or {}
        if a.get("state") == settings.asset_state_in_store and before.get("user_id") and aid not in marks:
            session.add(AssetReturn(asset_id=aid, from_user=before.get("user_name"), source="sync"))
            added += 1
    return added


async def run_sync(
    session: AsyncSession,
    *,
    trigger: str = "scheduled",
    requested_by: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    settings = await load_module_settings(session)
    run = SyncRun(trigger=trigger, requested_by=requested_by, status="running", stats={}, errors={}, notes=[])
    session.add(run)
    await session.commit()
    run_id = run.id  # rollback expires `run`
    try:
        return await _sync(session, run, settings, requested_by=requested_by, today=today)
    except (Exception, asyncio.CancelledError) as exc:  # CancelledError: the scheduler's timeout
        # Never leave the run "running" forever: close it in a fresh transaction, then re-raise.
        try:
            await session.rollback()
        except Exception:
            log.exception("lifecycle_sync_rollback_failed", run_id=str(run_id))
        async with AsyncSession(session.bind, expire_on_commit=False) as s:
            failed = await s.get(SyncRun, run_id)
            if failed is not None:
                failed.status = "failure"
                failed.finished_at = datetime.now(UTC)
                failed.errors = {"crash": f"{type(exc).__name__}: {exc}"[:500]}
                await s.commit()
        raise


def asset_types(settings: LifecycleSettings) -> set[str]:
    """SDP product types kept from a sync: the reconciled types plus every type the allocation standard names
    (UPS, IP Phone…), so stock is never empty for a device the standard asks for. Empty = all types."""
    if not settings.asset_product_types:
        return set()
    names = [
        *settings.asset_product_types,
        *(t for i in settings.allocation_standard for t in i.product_types),
    ]
    return {t.strip().lower() for t in names if t.strip()}


async def _workstations(client: SdpClient, seen: set[str]) -> list[dict[str, Any]]:
    """Computers (laptops, PCs) SDP keeps as workstations, which its asset list may leave out. Optional: an SDP
    without that endpoint (or a key without access to it) just adds nothing."""
    try:
        rows = await client.list_all(
            "/api/v3/workstations", "workstations", sort_field="id", sort_order="asc"
        )
    except Exception as exc:
        log.warning("lifecycle_sdp_workstations_unreadable", error=_err(exc))
        return []
    return [w for w in rows if str(w.get("id")) not in seen]


# shortcut: at most this many SDP searches per sync; raise if many active staff are missing from the user list.
MAX_SDP_LOOKUPS = 100


async def _find_missing_sdp_users(
    session: AsyncSession,
    settings: LifecycleSettings,
    sdp_users: list[dict[str, Any]],
    biostar_users: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """SDP users the full list did not return (offset paging over a shifting list can skip records): for each
    active BioStar user whose last 5 digits match no listed SDP employee ID, search SDP by employee ID."""
    known = {normalize.id_key(u["employee_id"]) for u in sdp_users if u.get("employee_id")}
    missing = sorted(
        {
            normalize.id_key(b["user_id"])
            for b in biostar_users
            if not b.get("retired") and b.get("user_id", "").isdigit()
        }
        - known
    )[:MAX_SDP_LOOKUPS]
    if not missing:
        return []
    seen = {u["id"] for u in sdp_users}
    found: list[dict[str, Any]] = []
    field = settings.sdp_user_employee_id_field
    try:
        _, ctx = await open_connection(session, "sdp", Purpose.READ, profile_id=settings.sdp_profile_id)
        async with SdpClient(ctx) as client:
            for key in missing:
                data = await client.get(
                    "/api/v3/users",
                    {
                        "row_count": 20,
                        "search_criteria": {"field": field, "condition": "contains", "value": key},
                    },
                )
                for raw in data.get("users") or []:
                    u = normalize.sdp_user(raw, field)
                    if u["id"] not in seen and u["employee_id"] and normalize.id_key(u["employee_id"]) == key:
                        seen.add(u["id"])
                        found.append(u)
    except Exception as exc:  # the lookup only adds matches: never fail the sync over it
        log.warning("lifecycle_sdp_user_lookup_failed", error=_err(exc), found=len(found))
    if found:
        log.info("lifecycle_sdp_users_missing_from_list", count=len(found))
    return found


async def _sync(
    session: AsyncSession,
    run: SyncRun,
    settings: LifecycleSettings,
    *,
    requested_by: str | None,
    today: date | None,
) -> dict[str, Any]:
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
                raw_users = await client.list_all("/api/v3/users", "users", sort_field="id", sort_order="asc")
                sdp_users = [normalize.sdp_user(u, settings.sdp_user_employee_id_field) for u in raw_users]
                sources["sdp"] = "requests" not in errors
            except Exception as exc:
                errors["sdp_users"] = _err(exc)
            try:
                raw_assets = await client.list_all(
                    "/api/v3/assets", "assets", sort_field="id", sort_order="asc"
                )
                raw_assets += await _workstations(client, {str(a.get("id")) for a in raw_assets})
                types = asset_types(settings)
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

    # Main BioStar, then the extra systems. BioStar counts as read only when
    # every system was read: never judge "no BioStar user" on a partial picture.
    main = main_biostar(settings)
    reads: list[tuple[str, dict[str, Any]]] = [("biostar", main)]
    reads += [
        (f"biostar:{s.profile_id}", {"profile_id": s.profile_id})
        for s in settings.biostar_sources
        if s.profile_id != main.get("profile_id")  # an extra source chosen as main is read once, as main
    ]
    read_users: list[dict[str, Any]] = []
    retired_groups = {g.strip().lower() for g in settings.biostar_retired_groups if g.strip()}
    excluded_groups = {g.strip().lower() for g in settings.biostar_excluded_groups if g.strip()}
    for err_key, which in reads:
        try:
            profile, ctx = await open_connection(session, "biostar", Purpose.READ, **which)
            async with BioStarClient(ctx) as client:
                for u in await client.list_users():
                    n = normalize.biostar_user(u)
                    if (n.get("group") or "").strip().lower() in excluded_groups:
                        continue  # vendors, contractors…: not reconciled at all
                    if (n.get("group") or "").strip().lower() in retired_groups:
                        n["retired"], n["active"] = True, False  # moved to the "left" group = offboarded
                    n["source"] = profile.name
                    # Snapshot key: the raw user id is only unique within one BioStar system.
                    n["ref"] = (
                        n["user_id"] if err_key == "biostar" else f"{n['user_id']}@{profile.name}"[:128]
                    )
                    read_users.append(n)
        except Exception as exc:
            errors[err_key] = _err(exc)
    sources["biostar"] = not any(k.startswith("biostar") for k in errors)
    # SDP is the reference: BioStar and SDP employee IDs are matched on their last 5 digits.
    if sources["sdp"] and sdp_users:
        sdp_users += await _find_missing_sdp_users(session, settings, sdp_users, read_users)
    sdp_ids = normalize.sdp_id_index(sdp_users)
    for n in read_users:
        n["employee_id"] = normalize.resolve_employee_id(n["user_id"], n.get("name") or "", sdp_ids)
    bs_users = normalize.merge_biostar_users(read_users)
    if sources["assets"]:
        stats["assets_returned"] = await track_returns(session, assets, settings)

    for system, items, key in (
        ("sdp_user", sdp_users, "id"),
        ("sdp_asset", assets, "id"),
        ("biostar_user", bs_users, "ref"),
    ):
        session.add_all(
            Snapshot(
                run_id=run.id,
                system=system,
                external_id=str(i[key]),
                employee_id=i.get("employee_id"),
                data=i,
            )
            for i in items
        )

    requests = [request_dict(r) for r in (await session.scalars(select(LifecycleRequest))).all()]
    result = reconcile(requests, sdp_users, assets, bs_users, settings, today or today_vn(), sources)
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
            "biostar_users": sum(1 for b in bs_users if not b.get("retired")),
            "biostar_retired": sum(1 for b in bs_users if b.get("retired")),
            "employees": len(result.rows),
            "issues": issue_total,
        }
    )
    if sources["sdp"]:
        # One draft job per onboard/offboard request (drafts never write to external systems).
        from app.modules.lifecycle.jobs_api import auto_create_jobs

        stats["jobs_created"] = await auto_create_jobs(session)
        from app.modules.lifecycle.assignment import sync_assignees

        stats["jobs_reassigned"] = await sync_assignees(session)
    run.stats = stats
    run.errors = errors
    run.notes = result.notes
    run.finished_at = datetime.now(UTC)
    run.status = (
        "success"
        if all(sources.values()) and not errors
        else ("failure" if not any(sources.values()) else "partial")
    )

    # Retention: raw snapshots are only read for the latest data run (queries.latest_run), so keep the
    # two newest data runs (the previous one for readers still on it); ReconRow keeps the configured
    # retention for history, and run summaries are kept for trends.
    keep = (
        select(SyncRun.id)
        .where(SyncRun.finished_at.is_not(None), SyncRun.status.in_(["success", "partial"]))
        .order_by(SyncRun.started_at.desc())
        .limit(2)
    )
    await session.execute(delete(Snapshot).where(Snapshot.run_id != run.id, Snapshot.run_id.not_in(keep)))
    cutoff = datetime.now(UTC) - timedelta(days=settings.snapshot_retention_days)
    old_runs = select(SyncRun.id).where(SyncRun.started_at < cutoff)
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
