from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.connections.models import ConnectionProfile
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.biostar import BioStarClient
from app.core.db import get_session
from app.core.errors import NotFound
from app.core.scheduler.service import get_task, request_run, task_out
from app.core.settings.service import save_settings
from app.modules.lifecycle.export import to_csv, to_xlsx
from app.modules.lifecycle.models import IssueAck, LifecycleRequest, ReconRow, Snapshot, SyncRun
from app.modules.lifecycle.parsing import classify_change_type, fold, html_to_text
from app.modules.lifecycle.queries import filtered_rows, latest_run, run_out
from app.modules.lifecycle.reconcile import RULES
from app.modules.lifecycle.settings import (
    DEFAULT_LABELS,
    FIELD_TITLES,
    MODULE_ID,
    SETTINGS_KEY,
    LifecycleSettings,
    main_biostar,
    matrix_from_standard,
)
from app.modules.lifecycle.settings import load as load_module_settings
from app.modules.lifecycle.sync import SYNC_TASK_KEY, reparse_all

router = APIRouter(tags=["lifecycle"])
view = require("lifecycle.reconciliation.view")
sync_perm = require("lifecycle.reconciliation.sync")
manage = require("lifecycle.settings.manage")


# ---------------------------------------------------------------- reconciliation
@router.get("/rules")
async def list_rules(_: CurrentUser = Depends(view)) -> list[dict[str, str]]:
    return [{"code": k, "severity": v[0], "system": v[1], "title": v[2]} for k, v in RULES.items()]


@router.get("/reconciliation")
async def reconciliation(
    _: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    q: str = Query("", max_length=100),
    only_issues: bool = False,
    severity: Literal["", "high", "medium", "low"] = "",
    rule: str = "",
    kind: str = "",
    department: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    run = await latest_run(session)
    if run is None:
        return {"run": None, "total": 0, "items": [], "facets": {}, "notes": []}
    items, facets = await filtered_rows(
        session,
        run,
        q=q,
        only_issues=only_issues,
        severity=severity,
        rule=rule,
        kind=kind,
        department=department,
    )
    return {
        "run": run_out(run),
        "total": len(items),
        "items": items[offset : offset + limit],
        "facets": facets,
        "notes": run.notes,
    }


@router.get("/reconciliation/export")
async def export_reconciliation(
    user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    format: Literal["csv", "xlsx"] = "xlsx",
    q: str = Query("", max_length=100),
    only_issues: bool = False,
    severity: Literal["", "high", "medium", "low"] = "",
    rule: str = "",
    kind: str = "",
    department: str = "",
) -> Response:
    run = await latest_run(session)
    if run is None:
        raise NotFound("Chưa có dữ liệu đối soát. Chạy đồng bộ trước.")
    items, _ = await filtered_rows(
        session,
        run,
        q=q,
        only_issues=only_issues,
        severity=severity,
        rule=rule,
        kind=kind,
        department=department,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.reconciliation_exported",
        message=f"{len(items)} rows, {format}",
    )
    await session.commit()
    if format == "csv":
        return Response(
            to_csv(items),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="doi-soat-{stamp}.csv"'},
        )
    return Response(
        to_xlsx(items, "Đối soát theo Mã NV"),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="doi-soat-{stamp}.xlsx"'},
    )


@router.get("/reconciliation/{employee_id}")
async def reconciliation_detail(
    employee_id: str, _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    run = await latest_run(session)
    row = None
    if run is not None:
        row = await session.scalar(
            select(ReconRow).where(ReconRow.run_id == run.id, ReconRow.employee_id == employee_id)
        )
    if row is None:
        raise NotFound(f"Không có dữ liệu cho Mã NV {employee_id} trong lần đồng bộ gần nhất.")
    data = row.data
    wanted = [*data.get("sdp_user_ids", []), *data.get("asset_ids", [])]
    if data.get("biostar_user_id"):
        wanted.append(data["biostar_user_id"])
    snaps = (
        await session.scalars(
            select(Snapshot).where(Snapshot.run_id == row.run_id, Snapshot.external_id.in_(wanted))
        )
    ).all()
    sdp_users = [
        s.data for s in snaps if s.system == "sdp_user" and s.external_id in data.get("sdp_user_ids", [])
    ]
    assets = [s.data for s in snaps if s.system == "sdp_asset" and s.external_id in data.get("asset_ids", [])]
    bs = next(
        (
            s.data
            for s in snaps
            if s.system == "biostar_user" and s.external_id == data.get("biostar_user_id")
        ),
        None,
    )
    reqs = (
        await session.scalars(
            select(LifecycleRequest)
            .where(LifecycleRequest.employee_id == employee_id)
            .order_by(LifecycleRequest.created_at.desc())
        )
    ).all()
    history = (
        await session.execute(
            select(SyncRun.started_at, ReconRow.issue_count, ReconRow.max_severity)
            .join(SyncRun, SyncRun.id == ReconRow.run_id)
            .where(ReconRow.employee_id == employee_id)
            .order_by(SyncRun.started_at.desc())
            .limit(20)
        )
    ).all()
    acks = (await session.scalars(select(IssueAck).where(IssueAck.employee_id == employee_id))).all()
    return {
        "row": {**data, "issue_count": row.issue_count, "max_severity": row.max_severity},
        "requests": [_request_out(r) for r in reqs],
        "sdp_users": sdp_users,
        "assets": assets,
        "biostar": bs,
        "history": [{"at": h[0], "issue_count": h[1], "max_severity": h[2]} for h in history],
        "acks": [
            {"rule": a.rule, "note": a.note, "acked_by": a.acked_by, "acked_at": a.acked_at} for a in acks
        ],
    }


class AckIn(BaseModel):
    rule: str
    note: str = Field("", max_length=1000)


async def _refresh_latest_row(session: AsyncSession, employee_id: str) -> None:
    run = await latest_run(session)
    if run is None:
        return
    row = await session.scalar(
        select(ReconRow).where(ReconRow.run_id == run.id, ReconRow.employee_id == employee_id)
    )
    if row is None:
        return
    acks = {
        a.rule
        for a in (await session.scalars(select(IssueAck).where(IssueAck.employee_id == employee_id))).all()
    }
    data = dict(row.data)
    data["issues"] = [{**i, "acked": i["rule"] in acks} for i in data.get("issues", [])]
    open_issues = [i for i in data["issues"] if not i["acked"]]
    row.data = data
    row.issue_count = len(open_issues)
    row.max_severity = max(
        (i["severity"] for i in open_issues), key={"high": 3, "medium": 2, "low": 1}.__getitem__, default=None
    )


@router.post("/reconciliation/{employee_id}/ack", status_code=204)
async def ack_issue(
    employee_id: str,
    body: AckIn,
    user: CurrentUser = Depends(sync_perm),
    session: AsyncSession = Depends(get_session),
) -> None:
    if body.rule not in RULES:
        raise NotFound("Quy tắc đối soát không tồn tại.")
    ack = await session.get(IssueAck, (employee_id, body.rule))
    if ack is None:
        session.add(IssueAck(employee_id=employee_id, rule=body.rule, note=body.note, acked_by=user.upn))
    else:
        ack.note = body.note
        ack.acked_by = user.upn
    await session.flush()
    await _refresh_latest_row(session, employee_id)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.issue_acknowledged",
        target_type="employee",
        target_id=employee_id,
        target_label=employee_id,
        after={"rule": body.rule, "note": body.note},
    )
    await session.commit()


@router.delete("/reconciliation/{employee_id}/ack/{rule}", status_code=204)
async def unack_issue(
    employee_id: str,
    rule: str,
    user: CurrentUser = Depends(sync_perm),
    session: AsyncSession = Depends(get_session),
) -> None:
    ack = await session.get(IssueAck, (employee_id, rule))
    if ack is None:
        raise NotFound("Lệch này chưa được đánh dấu.")
    await session.delete(ack)
    await session.flush()
    await _refresh_latest_row(session, employee_id)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.issue_reopened",
        target_type="employee",
        target_id=employee_id,
        target_label=employee_id,
        before={"rule": rule},
    )
    await session.commit()


# ---------------------------------------------------------------- RTR requests
def _request_out(r: LifecycleRequest, full: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": r.id,
        "display_id": r.display_id,
        "subject": r.subject,
        "status": r.status,
        "created_at": r.created_at,
        "employee_id": r.employee_id,
        "full_name": r.full_name,
        "change_type_raw": r.change_type_raw,
        "kind": r.kind,
        "effective_date": r.effective_date,
        "department": (r.fields or {}).get("department"),
        "parse_errors": r.parse_errors,
    }
    if full:
        out["fields"] = r.fields
        out["description_text"] = html_to_text(r.description_text)
        out["field_titles"] = FIELD_TITLES
    return out


@router.get("/requests")
async def list_requests(
    _: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    q: str = Query("", max_length=100),
    kind: str = "",
    errors_only: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    reqs = (
        await session.scalars(
            select(LifecycleRequest)
            .options(defer(LifecycleRequest.description_text))  # only the detail view shows it
            .order_by(LifecycleRequest.created_at.desc())
        )
    ).all()
    needle = fold(q) if q else ""
    items = [
        r
        for r in reqs
        if (not needle or needle in fold(f"{r.display_id} {r.employee_id} {r.full_name} {r.subject}"))
        and (not kind or (r.kind or "unknown") == kind)
        and (not errors_only or r.parse_errors)
    ]
    counts = Counter(r.kind or "unknown" for r in reqs)
    return {
        "total": len(items),
        "items": [_request_out(r) for r in items[offset : offset + limit]],
        "kinds": dict(counts),
        "with_errors": sum(1 for r in reqs if r.parse_errors),
    }


@router.get("/requests/{request_id}")
async def get_request(
    request_id: str, _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    r = await session.get(LifecycleRequest, request_id)
    if r is None:
        raise NotFound("Không tìm thấy request.")
    return _request_out(r, full=True)


@router.get("/change-types")
async def change_types(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    settings = await load_module_settings(session)
    raws = Counter(r for r in (await session.scalars(select(LifecycleRequest.change_type_raw))).all() if r)
    explicit = {fold(k): v for k, v in settings.change_type_map.items()}
    return [
        {
            "raw": raw,
            "count": n,
            "kind": classify_change_type(raw, settings),
            "explicit": explicit.get(fold(raw)),
        }
        for raw, n in raws.most_common()
    ]


# ---------------------------------------------------------------- sync
@router.get("/sync")
async def sync_status(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    task = await get_task(session, SYNC_TASK_KEY)
    runs = (await session.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(20))).all()
    return {"task": task_out(task) if task else None, "runs": [run_out(r) for r in runs]}


@router.get("/sync/runs/{run_id}")
async def sync_run_detail(
    run_id: str, _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    run = await session.get(SyncRun, run_id)
    if run is None:
        raise NotFound("Không tìm thấy lần đồng bộ.")
    return {**(run_out(run) or {}), "notes": run.notes}


@router.post("/sync")
async def sync_now(
    user: CurrentUser = Depends(sync_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    row = await request_run(session, SYNC_TASK_KEY, user.upn)
    await record(session, actor=user.actor, module_id=MODULE_ID, action="lifecycle.sync_requested")
    await session.commit()
    return task_out(row)


# ---------------------------------------------------------------- settings
@router.get("/biostar/user-groups")
async def biostar_groups(
    _: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """User groups read live from every BioStar system (to pick excluded / retired groups), with the user
    count of the latest sync. A system that cannot be read falls back to the groups that sync saw."""
    settings = await load_module_settings(session)
    counts: dict[str, int] = {}
    run = await latest_run(session)
    if run is not None:
        for data in (
            await session.scalars(
                select(Snapshot.data).where(Snapshot.run_id == run.id, Snapshot.system == "biostar_user")
            )
        ).all():
            if group := (data.get("group") or "").strip():
                counts[group] = counts.get(group, 0) + 1
    names: set[str] = set()
    errors: list[str] = []
    reads = [main_biostar(settings)] + [{"profile_id": s.profile_id} for s in settings.biostar_sources]
    for which in reads:
        try:
            _, ctx = await open_connection(session, "biostar", Purpose.READ, **which)
            async with BioStarClient(ctx) as client:
                names |= {str(g.get("name")).strip() for g in await client.user_groups() if g.get("name")}
        except Exception as exc:
            errors.append(getattr(exc, "message", None) or str(exc))
    names |= set(counts)
    return {
        "items": [{"name": n, "users": counts.get(n)} for n in sorted(names, key=str.lower)],
        "errors": errors,
    }


@router.get("/settings")
async def get_module_settings(
    _: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = await load_module_settings(session)
    profiles = (
        await session.scalars(
            select(ConnectionProfile)
            .where(
                ConnectionProfile.system_type.in_(
                    ["sdp", "biostar", "exchange", "active_directory", "msgraph"]
                )
            )
            .order_by(ConnectionProfile.name)
        )
    ).all()
    return {
        "values": settings.model_dump(mode="json")
        | (
            # Not saved yet: start the matrix editor from the hand-written lines.
            {"allocation_matrix": matrix_from_standard(settings.allocation_standard).model_dump(mode="json")}
            if settings.allocation_matrix is None
            else {}
        ),
        "field_titles": FIELD_TITLES,
        "default_labels": DEFAULT_LABELS,
        "profiles": [
            {
                "id": str(p.id),
                "name": p.name,
                "system_type": p.system_type,
                "environment": p.environment,
                "enabled": p.enabled,
            }
            for p in profiles
        ],
    }


@router.put("/settings")
async def put_module_settings(
    body: dict[str, Any], user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = await save_settings(session, user.actor, MODULE_ID, SETTINGS_KEY, LifecycleSettings, body)
    reparsed = await reparse_all(session, settings)
    await session.commit()
    return {"values": settings.model_dump(mode="json"), "reparsed": reparsed}
