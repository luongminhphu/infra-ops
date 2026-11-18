from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser, current_user
from app.core.db import get_session
from app.core.errors import PermissionDenied
from app.core.modules.registry import get_registry
from app.core.scheduler.service import request_run
from app.core.workflow import engine
from app.core.workflow.definitions import WorkflowDefinition
from app.core.workflow.models import Job, JobEvent

router = APIRouter(tags=["workflow"])


def _wf(job: Job) -> WorkflowDefinition:
    return engine.workflow_or_404(job.workflow_key)[1]


def job_out(
    job: Job, wf: WorkflowDefinition | None = None, events: list[JobEvent] | None = None
) -> dict[str, Any]:
    wf = wf or _wf(job)
    defs = {s.key: s for s in wf.steps}
    done = sum(1 for s in job.steps if s.status in ("done", "skipped"))
    out: dict[str, Any] = {
        "id": str(job.id),
        "workflow_key": job.workflow_key,
        "workflow_title": wf.title,
        "status": job.status,
        "subject_key": job.subject_key,
        "subject_label": job.subject_label,
        "external_ref": job.external_ref,
        "params": job.params,
        "scheduled_for": job.scheduled_for,
        "current_step": job.current_step,
        "created_by": job.created_by,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "started_by": job.started_by,
        "started_at": job.started_at,
        "closed_at": job.closed_at,
        "close_reason": job.close_reason,
        "version": job.version,
        "progress": {"done": done, "total": len(job.steps)},
        "steps": [
            {
                "key": s.key,
                "title": s.title,
                "kind": s.kind,
                "status": s.status,
                "seq": s.seq,
                "description": defs[s.key].description if s.key in defs else "",
                "writes": list(defs[s.key].writes) if s.key in defs else [],
                "skippable": defs[s.key].skippable if s.key in defs else False,
                "requires_confirm": s.requires_confirm,
                "confirmed_by": s.confirmed_by,
                "confirmed_at": s.confirmed_at,
                "confirm_text": defs[s.key].confirm_text if s.key in defs else "",
                "attempts": s.attempts,
                "next_run_at": s.next_run_at,
                "waiting_since": s.waiting_since,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "actor": s.actor,
                "message": s.message,
                "before_state": s.before_state,
                "output": s.output,
            }
            for s in sorted(job.steps, key=lambda s: s.seq)
        ],
    }
    if events is not None:
        out["events"] = [
            {"at": e.at, "step_key": e.step_key, "level": e.level, "message": e.message, "actor": e.actor}
            for e in events
        ]
    return out


async def _load(
    session: AsyncSession,
    user: CurrentUser,
    job_id: uuid.UUID,
    which: Literal["view", "execute", "override"],
    for_update: bool = False,
) -> tuple[Job, WorkflowDefinition]:
    job = await engine.get_job(session, job_id, for_update=for_update)
    wf = _wf(job)
    engine.need(
        user.permissions,
        {"view": wf.view_permission, "execute": wf.execute_permission, "override": wf.override_permission}[
            which
        ],
    )
    return job, wf


async def _done(session: AsyncSession, user: CurrentUser, job: Job, wf: WorkflowDefinition) -> dict[str, Any]:
    await request_run(session, engine.PUMP_TASK_KEY, user.upn)
    await session.commit()
    await session.refresh(job)
    events = (
        await session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job.id)
            .order_by(JobEvent.at.desc(), JobEvent.id.desc())
            .limit(200)
        )
    ).all()
    return job_out(job, wf, list(events))


@router.get("/jobs")
async def list_jobs(
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    workflow: str = "",
    status: str = "",
    subject_key: str = "",
    q: str = Query("", max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    allowed = [w.key for _, w in get_registry().workflows() if user.has(w.view_permission)]
    if workflow and workflow not in allowed:
        raise PermissionDenied("Bạn không có quyền xem quy trình này.")
    stmt = select(Job).where(Job.workflow_key.in_([workflow] if workflow else allowed))
    if status == "open":
        stmt = stmt.where(Job.status.notin_(["completed", "cancelled"]))
    elif status:
        stmt = stmt.where(Job.status == status)
    if subject_key:
        stmt = stmt.where(Job.subject_key == subject_key)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Job.subject_label).like(like),
                Job.subject_key.like(like),
                Job.external_ref.like(like),
            )
        )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    counts = dict(
        (
            await session.execute(
                select(Job.status, func.count()).where(Job.workflow_key.in_(allowed)).group_by(Job.status)
            )
        ).all()
    )
    rows = (await session.scalars(stmt.order_by(Job.updated_at.desc()).offset(offset).limit(limit))).all()
    return {"total": total, "counts": counts, "items": [job_out(j) for j in rows]}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: uuid.UUID, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "view")
    events = (
        await session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job.id)
            .order_by(JobEvent.at.desc(), JobEvent.id.desc())
            .limit(200)
        )
    ).all()
    return job_out(job, wf, list(events))


@router.get("/jobs/{job_id}/preview")
async def preview(
    job_id: uuid.UUID, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    job, _ = await _load(session, user, job_id, "view")
    return await engine.preview(session, user.actor, job)


class StartIn(BaseModel):
    version: int
    scheduled_for: date | None = None


@router.post("/jobs/{job_id}/start")
async def start(
    job_id: uuid.UUID,
    body: StartIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    engine.check_version(job, body.version)
    await engine.start_job(session, user.actor, job, body.scheduled_for)
    return await _done(session, user, job, wf)


class ConfirmIn(BaseModel):
    typed: str = Field(max_length=64)


@router.post("/jobs/{job_id}/steps/{key}/confirm")
async def confirm(
    job_id: uuid.UUID,
    key: str,
    body: ConfirmIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    await engine.confirm_step(session, user.actor, job, key, body.typed)
    return await _done(session, user, job, wf)


class CompleteIn(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)


@router.post("/jobs/{job_id}/steps/{key}/complete")
async def complete(
    job_id: uuid.UUID,
    key: str,
    body: CompleteIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    await engine.complete_manual(session, user.actor, job, key, body.data)
    return await _done(session, user, job, wf)


@router.post("/jobs/{job_id}/steps/{key}/retry")
async def retry(
    job_id: uuid.UUID,
    key: str,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    await engine.retry_step(session, user.actor, job, key)
    return await _done(session, user, job, wf)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/jobs/{job_id}/steps/{key}/skip")
async def skip(
    job_id: uuid.UUID,
    key: str,
    body: ReasonIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    await engine.skip_step(session, user.actor, job, key, body.reason)
    return await _done(session, user, job, wf)


@router.post("/jobs/{job_id}/cancel")
async def cancel(
    job_id: uuid.UUID,
    body: ReasonIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    await engine.cancel_job(session, user.actor, job, body.reason)
    return await _done(session, user, job, wf)


@router.post("/jobs/{job_id}/override-close")
async def override_close(
    job_id: uuid.UUID,
    body: ReasonIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "override", for_update=True)
    await engine.override_close(session, user.actor, job, body.reason)
    return await _done(session, user, job, wf)
