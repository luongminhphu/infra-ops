"""Lifecycle-specific job helpers: create a job from an RTR request, edit the draft, pick assets."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor, record
from app.core.auth.deps import CurrentUser, require
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.biostar import BioStarClient
from app.core.db import get_session
from app.core.errors import AppError, Conflict, NotFound
from app.core.workflow import engine
from app.core.workflow.api import job_out
from app.core.workflow.models import Job
from app.modules.lifecycle.assignment import ASSIGN, JOB_WORKFLOWS, assign_from_request, assignment_params
from app.modules.lifecycle.models import LifecycleRequest, ReconRow, Snapshot
from app.modules.lifecycle.parsing import fold
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import MODULE_ID, main_biostar
from app.modules.lifecycle.settings import load as load_module_settings
from app.modules.lifecycle.workflows import WORKFLOW_BY_KIND

router = APIRouter(tags=["lifecycle-jobs"])
view = require("lifecycle.job.view")
execute = require("lifecycle.job.execute")


async def _sdp_user_guess(session: AsyncSession, employee_id: str) -> tuple[str | None, str | None]:
    run = await latest_run(session)
    if run is None:
        return None, None
    row = await session.scalar(
        select(ReconRow).where(ReconRow.run_id == run.id, ReconRow.employee_id == employee_id)
    )
    ids = (row.data.get("sdp_user_ids") if row else None) or []
    if len(ids) != 1:
        return None, None
    snap = await session.scalar(
        select(Snapshot).where(
            Snapshot.run_id == run.id, Snapshot.system == "sdp_user", Snapshot.external_id == ids[0]
        )
    )
    return ids[0], (snap.data.get("name") if snap else None)


def job_params(req: LifecycleRequest) -> dict[str, Any]:
    f = req.fields or {}
    dept, unit = f.get("department") or "", f.get("unit")
    return {
        "kind": req.kind,
        "request_id": req.id,
        "request_display_id": req.display_id,
        "effective_date": req.effective_date.isoformat() if req.effective_date else None,
        "employee": {
            "id": req.employee_id,
            "full_name": req.full_name or "",
            "department": f"{dept} – {unit}" if dept and unit else dept,
            "job_title": f.get("job_title") or "",
            "work_location": f.get("work_location") or "",
        },
        "assets": [],
        "sdp_user_id": None,
        "sdp_user_name": None,
    }


async def create_job_for_request(
    session: AsyncSession, actor: Actor, req: LifecycleRequest
) -> tuple[Job, bool]:
    if req.kind not in WORKFLOW_BY_KIND:
        raise AppError(
            "Chỉ tạo Job cho request Nhân viên mới, Nghỉ việc hoặc Thuyên chuyển. "
            "Phân loại request ở Cấu hình module nếu cần.",
            code="unsupported_kind",
        )
    if not req.employee_id:
        raise AppError(
            "Request chưa có Mã NV hợp lệ. Sửa nội dung request trên SDP rồi đồng bộ lại.", code="no_employee"
        )
    wf = WORKFLOW_BY_KIND[req.kind]
    params = job_params(req)
    params["sdp_user_id"], params["sdp_user_name"] = await _sdp_user_guess(session, req.employee_id)
    if params["sdp_user_id"] is None:
        params["sdp_user_name"] = None
    job, new = await engine.create_job(
        session,
        actor,
        wf.key,
        params=params,
        subject_key=req.employee_id,
        subject_label=f"{req.full_name or req.employee_id} ({req.employee_id})",
        external_ref=req.id,
        idempotency_key=f"lifecycle:{req.kind}:{req.id}",
    )
    if new:
        await session.flush()
        await assign_from_request(session, job, req)
    return job, new


async def auto_create_jobs(session: AsyncSession) -> int:
    """Called after a sync: one draft job per onboard/offboard request (drafts never touch external systems)."""
    settings = await load_module_settings(session)
    if not settings.auto_create_jobs:
        return 0
    reqs = (
        await session.scalars(
            select(LifecycleRequest).where(
                LifecycleRequest.kind.in_(list(WORKFLOW_BY_KIND)), LifecycleRequest.employee_id.is_not(None)
            )
        )
    ).all()
    existing = set(
        (await session.scalars(select(Job.idempotency_key).where(Job.module_id == MODULE_ID))).all()
    )
    closed = {s.lower() for s in settings.sdp_closed_statuses}
    created = 0
    for r in reqs:
        if r.parse_errors or (r.status or "").lower() in closed or f"lifecycle:{r.kind}:{r.id}" in existing:
            continue
        _, new = await create_job_for_request(session, Actor(None, "system (đồng bộ)"), r)
        created += int(new)
    return created


class CreateIn(BaseModel):
    request_id: str = Field(max_length=32)


@router.post("/jobs", status_code=201)
async def create_job(
    body: CreateIn, user: CurrentUser = Depends(execute), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    req = await session.get(LifecycleRequest, body.request_id)
    if req is None:
        raise NotFound("Không tìm thấy request. Đồng bộ dữ liệu trước.")
    job, _ = await create_job_for_request(session, user.actor, req)
    await session.commit()
    await session.refresh(job)
    return job_out(job)


class AssetIn(BaseModel):
    id: str = Field(max_length=32)
    name: str = Field(max_length=200)
    tag: str | None = Field(None, max_length=100)
    product_type: str | None = Field(None, max_length=100)


class ParamsIn(BaseModel):
    version: int
    assets: list[AssetIn] | None = Field(None, max_length=50)
    sdp_user_id: str | None = Field(None, max_length=32)
    sdp_user_name: str | None = Field(None, max_length=200)
    clear_sdp_user: bool = False


@router.put("/jobs/{job_id}/params")
async def update_params(
    job_id: uuid.UUID,
    body: ParamsIn,
    user: CurrentUser = Depends(execute),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job = await engine.get_job(session, job_id, for_update=True)
    if job.module_id != MODULE_ID:
        raise NotFound("Không tìm thấy Job.")
    engine.check_version(job, body.version)
    if job.status != "draft":
        raise Conflict("Chỉ sửa được Job ở trạng thái nháp.", code="job_not_draft")
    before = {
        "assets": [a["id"] for a in job.params.get("assets", [])],
        "sdp_user_id": job.params.get("sdp_user_id"),
    }
    params = dict(job.params)
    if body.assets is not None:
        params["assets"] = [a.model_dump() for a in body.assets]
    if body.clear_sdp_user:
        params["sdp_user_id"] = params["sdp_user_name"] = None
    elif body.sdp_user_id:
        params["sdp_user_id"], params["sdp_user_name"] = body.sdp_user_id, body.sdp_user_name
    job.params = params
    engine.touch(job)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workflow.job_params_updated",
        target_type="job",
        target_id=str(job.id),
        target_label=job.subject_label,
        before=before,
        after={
            "assets": [a["id"] for a in params.get("assets", [])],
            "sdp_user_id": params.get("sdp_user_id"),
        },
    )
    await session.commit()
    await session.refresh(job)
    return job_out(job)


@router.get("/assets/in-store")
async def in_store_assets(
    _: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    q: str = Query("", max_length=100),
    product_type: str = "",
) -> dict[str, Any]:
    """In Store assets from the latest sync (re-checked live when the step runs)."""
    settings = await load_module_settings(session)
    run = await latest_run(session)
    if run is None:
        return {"items": [], "as_of": None, "types": []}
    snaps = (
        await session.scalars(
            select(Snapshot).where(Snapshot.run_id == run.id, Snapshot.system == "sdp_asset")
        )
    ).all()
    items = [
        s.data
        for s in snaps
        if s.data.get("state") == settings.asset_state_in_store and not s.data.get("user_id")
    ]
    types = sorted({a.get("product_type") or "" for a in items} - {""})
    needle = fold(q) if q else ""
    items = [
        a
        for a in items
        if (not product_type or a.get("product_type") == product_type)
        and (
            not needle
            or needle in fold(f"{a.get('name')} {a.get('asset_tag')} {a.get('serial')} {a.get('product')}")
        )
    ]
    items.sort(key=lambda a: (a.get("product_type") or "", a.get("name") or ""))
    return {"items": items[:200], "total": len(items), "as_of": run.finished_at, "types": types}


@router.get("/sdp-users")
async def sdp_users(
    _: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    q: str = Query("", min_length=2, max_length=100),
) -> list[dict[str, Any]]:
    run = await latest_run(session)
    if run is None:
        return []
    needle = fold(q)
    snaps = (
        await session.scalars(
            select(Snapshot).where(Snapshot.run_id == run.id, Snapshot.system == "sdp_user")
        )
    ).all()
    return [
        s.data
        for s in snaps
        if needle in fold(f"{s.data.get('name')} {s.data.get('email')} {s.data.get('employee_id')}")
    ][:20]


@router.get("/biostar/groups")
async def biostar_groups(
    _: CurrentUser = Depends(require("lifecycle.settings.manage")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    settings = await load_module_settings(session)
    _, ctx = await open_connection(session, "biostar", Purpose.READ, **main_biostar(settings))
    async with BioStarClient(ctx) as c:
        ug = await c.user_groups()
        ag = await c.access_groups()
    return {
        "user_groups": [{"id": str(g.get("id")), "name": g.get("name")} for g in ug],
        "access_groups": [{"id": str(g.get("id")), "name": g.get("name")} for g in ag],
    }


# ---------------------------------------------------------------- assignment
@router.get("/assignees")
async def assignees(
    _: CurrentUser = Depends(require(ASSIGN)), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    """Active people who can run jobs (and have an e-mail to find them on SDP)."""
    from app.core.auth.models import User
    from app.core.modules.registry import get_registry
    from app.core.rbac.service import permissions_for_user

    users = (
        await session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name))
    ).all()
    out = []
    for u in users:
        if (u.email or "@" in u.upn) and "lifecycle.job.execute" in await permissions_for_user(
            session, u.id, get_registry()
        ):
            out.append({"id": str(u.id), "name": u.display_name, "upn": u.upn})
    return out


class AssignIn(BaseModel):
    user_id: uuid.UUID
    reason: str = Field("", max_length=500)


@router.post("/jobs/{job_id}/assign", status_code=201)
async def assign_job(
    job_id: uuid.UUID,
    body: AssignIn,
    user: CurrentUser = Depends(require(ASSIGN)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Hand a job to another operator: the worker sets the request's technician on SDP, then the job."""
    from app.core.auth.models import User

    job = await engine.get_job(session, job_id)
    return await _request_assignment(session, user, job, await session.get(User, body.user_id), body.reason)


@router.post("/jobs/{job_id}/claim", status_code=201)
async def claim_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(execute),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """An operator takes an unassigned job ("Nhận Job"): same path as a reassignment, to themselves."""
    from app.core.auth.models import User

    job = await engine.get_job(session, job_id)
    if job.assignee_user_id is not None:
        raise Conflict(f"Job đã do {job.assignee_name or 'người khác'} phụ trách.", code="job_assigned")
    return await _request_assignment(session, user, job, await session.get(User, user.id), "Nhận Job")


async def _request_assignment(
    session: AsyncSession, user: CurrentUser, job: Any, target: Any, reason: str
) -> dict[str, Any]:
    if job.workflow_key not in JOB_WORKFLOWS:
        raise NotFound("Không tìm thấy Job onboard/offboard.")
    if job.status in ("completed", "cancelled"):
        raise Conflict("Job đã kết thúc.", code="job_finished")
    req = await session.get(LifecycleRequest, job.external_ref)
    if req is None or target is None or not target.is_active:
        raise NotFound("Không tìm thấy request hoặc người nhận.")
    if job.assignee_user_id == target.id:
        raise AppError(f"Job đang do {target.display_name} phụ trách.", code="same_assignee")
    assign_job_, _ = await engine.create_job(
        session,
        user.actor,
        "lifecycle.assign",
        params=assignment_params(job, req, target, user.upn, reason.strip()),
        subject_key=job.subject_key,
        subject_label=f"Chuyển {job.subject_label} cho {target.display_name}",
        external_ref=str(job.id),
    )
    await engine.start_job(session, user.actor, assign_job_, None)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.job_assign_requested",
        system_type="sdp",
        target_type="job",
        target_id=str(job.id),
        target_label=job.subject_label,
        before={"assignee": job.assignee_name},
        after={"assignee": target.display_name},
        message=reason.strip() or None,
    )
    await session.commit()
    return job_out(assign_job_)
