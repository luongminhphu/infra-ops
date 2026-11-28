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
from app.core.workflow.api import check_job_scope, job_out
from app.core.workflow.models import Job
from app.modules.lifecycle.assignment import ASSIGN, JOB_WORKFLOWS, assign_from_request, assignment_params
from app.modules.lifecycle.models import LifecycleRequest, ReconRow, Snapshot
from app.modules.lifecycle.parsing import fold, split_person
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import FIELD_TITLES, MODULE_ID, main_biostar
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
    params = {
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
            "manager_employee_id": split_person(f.get("manager"))[1],
            "phone": f.get("phone") or "",
            "division": f.get("division") or "",
            "department_only": dept,
            "unit": unit or "",
        },
        "options": {"mailbox_handover": False},
        "assets": [],
        "sdp_user_id": None,
        "sdp_user_name": None,
    }
    if req.kind == "transfer":
        # An absent key means "unchanged": nothing empty is ever written.
        emp = params["employee"]
        params["employee"] = {k: v for k, v in emp.items() if v or k in ("id", "full_name")}
        old = {k: v for k, v in (f.get("old") or {}).items() if v}
        employee_old = dict(old)
        if mid := split_person(old.get("manager"))[1]:
            employee_old["manager_employee_id"] = mid
        params["employee_old"] = employee_old
        params["sources"] = {
            **{k: "request" for k in FIELD_TITLES if f.get(k) and k not in ("change_type", "effective_date")},
            **{f"old.{k}": "request_old" for k in old},
        }
    return params


async def create_job_for_request(
    session: AsyncSession, actor: Actor, req: LifecycleRequest, key_suffix: str = ""
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
    settings = await load_module_settings(session)
    if settings.is_closed(req.status):
        raise AppError(
            f"Request #{req.display_id or req.id} đã đóng trên SDP (“{req.status}”) – không tạo Job.",
            code="request_closed",
        )
    wf = WORKFLOW_BY_KIND[req.kind]
    params = job_params(req)
    params["microsoft"] = settings.microsoft_enabled  # decides which steps the job gets
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
        idempotency_key=f"lifecycle:{req.kind}:{req.id}{key_suffix}",
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
    created = 0
    for r in reqs:
        if r.parse_errors or settings.is_closed(r.status) or f"lifecycle:{r.kind}:{r.id}" in existing:
            continue
        _, new = await create_job_for_request(session, Actor(None, "system (đồng bộ)"), r)
        created += int(new)
    return created


async def _requests_for(session: AsyncSession, jobs: list[Job]) -> dict[str, LifecycleRequest]:
    rows = await session.scalars(
        select(LifecycleRequest).where(LifecycleRequest.id.in_([j.external_ref for j in jobs]))
    )
    return {r.id: r for r in rows.all()}


async def cancel_closed_jobs(session: AsyncSession) -> int:
    """Called after a sync: a draft Job, or a failed one where no step did any work, whose RTR request is now closed
    on SDP leaves the queues – cancelled with the reason, marked `auto_cancelled_at` (= its closed_at) and audited.
    A failed job is cancelled only when the request itself was cancelled or it failed at the first check."""
    settings = await load_module_settings(session)
    jobs = (
        await session.scalars(
            select(Job)
            .where(
                Job.module_id == MODULE_ID, Job.status.in_(("draft", "failed")), Job.external_ref.is_not(None)
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    reqs = await _requests_for(session, list(jobs))
    cancelled = 0
    for job in jobs:
        req = reqs.get(job.external_ref or "")
        if req is None or not settings.is_closed(req.status) or engine.steps_with_work(job):
            continue
        if job.status == "failed" and not (
            settings.is_cancelled(req.status) or job.current_step == "check_request"
        ):
            continue
        tail = "Job nháp tự huỷ." if job.status == "draft" else "Job chưa ghi gì ra hệ thống nên tự huỷ."
        reason = f"Request #{req.display_id or req.id} đã đóng trên SDP (“{req.status}”) – {tail}"
        await engine.cancel_job(session, Actor(None, "system (đồng bộ)"), job, reason)
        assert job.closed_at is not None  # set by cancel_job
        job.params = {**job.params, "auto_cancelled_at": job.closed_at.isoformat()}
        cancelled += 1
    return cancelled


async def reopen_reopened_jobs(session: AsyncSession) -> int:
    """Called after a sync: a Job auto-cancelled because its request closed returns to draft when the request is open
    again. Jobs cancelled by a person carry no marker, or a stale one (closed_at moved on), and stay cancelled."""
    settings = await load_module_settings(session)
    jobs = (
        await session.scalars(
            select(Job)
            .where(Job.module_id == MODULE_ID, Job.status == "cancelled", Job.external_ref.is_not(None))
            .where(Job.params.has_key("auto_cancelled_at"))
            .with_for_update(skip_locked=True)
        )
    ).all()
    reqs = await _requests_for(session, list(jobs))
    reopened = 0
    for job in jobs:
        req = reqs.get(job.external_ref or "")
        if req is None or settings.is_closed(req.status):
            continue
        marker = job.params["auto_cancelled_at"]
        job.params = {k: v for k, v in job.params.items() if k != "auto_cancelled_at"}
        if job.closed_at is None or job.closed_at.isoformat() != marker:
            continue  # stale: a person cancelled it after the auto-cancel
        try:
            await engine.reopen_job(
                session,
                Actor(None, "system (đồng bộ)"),
                job,
                f"Request #{req.display_id or req.id} mở lại trên SDP",
            )
        except Conflict:  # job_has_work: leave it cancelled
            continue
        reopened += 1
    return reopened


class CreateIn(BaseModel):
    request_id: str = Field(max_length=32)


@router.post("/jobs", status_code=201)
async def create_job(
    body: CreateIn, user: CurrentUser = Depends(execute), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    req = await session.get(LifecycleRequest, body.request_id)
    if req is None:
        raise NotFound("Không tìm thấy request. Đồng bộ dữ liệu trước.")
    suffix = ""
    if req.kind in WORKFLOW_BY_KIND:
        # A cancelled job that did work cannot be reopened and holds the request's key: "huỷ và tạo lại" starts a
        # fresh one, unless another job for the request is still alive. Sync never does this (it keeps the key).
        old = await session.scalar(select(Job).where(Job.idempotency_key == f"lifecycle:{req.kind}:{req.id}"))
        if old is not None and old.status == "cancelled" and engine.steps_with_work(old):
            alive = await session.scalar(
                select(Job).where(
                    Job.workflow_key == old.workflow_key,
                    Job.external_ref == req.id,
                    Job.status != "cancelled",
                )
            )
            if alive is not None:
                return job_out(alive)
            suffix = f":{uuid.uuid4().hex[:8]}"
    job, _ = await create_job_for_request(session, user.actor, req, suffix)
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
    mailbox_handover: bool | None = None  # offboard drafts only


def _option_open(job: Job) -> bool:
    """The mailbox option can change until `ad_move_ban` is confirmed or has run (it reads the option when it runs)."""
    if job.status in ("completed", "cancelled"):
        return False
    st = next((x for x in job.steps if x.key == "ad_move_ban"), None)
    return st is not None and st.status in ("pending", "waiting") and st.confirmed_at is None


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
    check_job_scope(user, job)
    engine.check_version(job, body.version)
    if (body.assets is not None or body.sdp_user_id or body.clear_sdp_user) and job.status != "draft":
        raise Conflict("Chỉ sửa được Job ở trạng thái nháp.", code="job_not_draft")
    if body.mailbox_handover is not None:
        if job.params.get("kind") != "offboard" or not job.params.get("microsoft"):
            raise AppError(
                "Chỉ Job offboard có bước Microsoft mới có tuỳ chọn giữ mailbox.",
                code="option_not_applicable",
            )
        if job.status != "draft" and not _option_open(job):
            raise Conflict(
                "Không đổi được tuỳ chọn giữ mailbox: bước chuyển vào Ban_Users đã được xác nhận hoặc đã chạy.",
                code="option_locked",
            )
    before = {
        "assets": [a["id"] for a in job.params.get("assets", [])],
        "sdp_user_id": job.params.get("sdp_user_id"),
        "mailbox_handover": (job.params.get("options") or {}).get("mailbox_handover"),
    }
    params = dict(job.params)
    if body.mailbox_handover is not None:
        params["options"] = {**(params.get("options") or {}), "mailbox_handover": body.mailbox_handover}
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
            "mailbox_handover": (params.get("options") or {}).get("mailbox_handover"),
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
    from app.core.rbac.models import Role, RolePermission, UserRole

    # Same rule as permissions_for_user: an all-permissions role, or a role granting the code.
    can_execute = (
        select(UserRole.user_id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(
            RolePermission,
            (RolePermission.role_id == Role.id) & (RolePermission.permission_code == "lifecycle.job.execute"),
        )
        .where(Role.all_permissions.is_(True) | RolePermission.role_id.is_not(None))
    )
    users = (
        await session.scalars(
            select(User).where(User.is_active.is_(True), User.id.in_(can_execute)).order_by(User.display_name)
        )
    ).all()
    return [{"id": str(u.id), "name": u.display_name, "upn": u.upn} for u in users if u.email or "@" in u.upn]


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

    # Locked: two people pressing "Nhận" at once must not both take it.
    job = await engine.get_job(session, job_id, for_update=True)
    if job.assignee_user_id is not None:
        raise Conflict(f"Job đã do {job.assignee_name or 'người khác'} phụ trách.", code="job_assigned")
    # Taken a moment ago: the SDP write-back (an assignment job) has not finished yet.
    pending = await session.scalar(
        select(Job).where(
            Job.workflow_key == "lifecycle.assign",
            Job.external_ref == str(job.id),
            Job.status.notin_(["completed", "cancelled", "failed"]),
        )
    )
    if pending is not None:
        raise Conflict(
            f"{pending.params.get('to_name') or 'Người khác'} vừa nhận Job này.", code="job_being_assigned"
        )
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
