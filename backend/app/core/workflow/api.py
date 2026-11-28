from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, current_user, require
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AppError, PermissionDenied
from app.core.modules.registry import get_registry
from app.core.scheduler.service import request_run
from app.core.storage.service import check_size, read_file, safe_filename, save_file, sniff
from app.core.workflow import checklist as cl
from app.core.workflow import engine
from app.core.workflow.definitions import WorkflowDefinition
from app.core.workflow.models import Job, JobEvent, JobEvidence, WorkflowChecklist

CHECKLIST_MANAGE = "core.workflow.checklist.manage"

router = APIRouter(tags=["workflow"])


def _wf(job: Job) -> WorkflowDefinition:
    return engine.workflow_or_404(job.workflow_key)[1]


STALLED_AFTER = timedelta(minutes=10)


def stalled_minutes(job: Job) -> int | None:
    if job.status != "running" or job.updated_at is None:
        return None
    idle = datetime.now(UTC) - job.updated_at
    return int(idle.total_seconds() // 60) if idle > STALLED_AFTER else None


LEAN_STEP_OMIT = ("before_state", "output", "checklist", "evidence", "message", "verify_note")


def job_out(
    job: Job,
    wf: WorkflowDefinition | None = None,
    events: list[JobEvent] | None = None,
    lean: bool = False,
) -> dict[str, Any]:
    """``lean`` (list views): omits assignments and heavy step fields, so evidence/assignments may be raiseloaded."""
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
        # A running job is advanced by the worker every few seconds; no change for this long = stalled
        # (worker down, or a step stuck). Surfaced in the UI instead of a silent "Đang chạy".
        "stalled_minutes": stalled_minutes(job),
        "checklist_version": job.checklist_version,
        "assignee": {
            "user_id": str(job.assignee_user_id) if job.assignee_user_id else None,
            "name": job.assignee_name,
        }
        if job.assignee_name
        else None,
        "assignments": []
        if lean
        else [
            {
                "from_name": a.from_name,
                "to_name": a.to_name,
                "step_key": a.step_key,
                "source": a.source,
                "actor": a.actor,
                "reason": a.reason,
                "at": a.at,
            }
            for a in job.assignments
        ],
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
                "checklist": s.checklist,
                "evidence_required": s.evidence_required,
                "verified_by": s.verified_by,
                "verified_at": s.verified_at,
                "verify_note": s.verify_note,
                "evidence": [] if lean else [cl.evidence_out(e) for e in job.evidence if e.step_key == s.key],
            }
            for s in sorted(job.steps, key=lambda s: s.seq)
        ],
    }
    if lean:
        del out["assignments"]
        for st in out["steps"]:
            for k in LEAN_STEP_OMIT:
                del st[k]
    if events is not None:
        out["events"] = [
            {"at": e.at, "step_key": e.step_key, "level": e.level, "message": e.message, "actor": e.actor}
            for e in events
        ]
    return out


def _sees_all(user: CurrentUser, wf: WorkflowDefinition) -> bool:
    return wf.view_all_permission is None or user.has(wf.view_all_permission)


async def _load(
    session: AsyncSession,
    user: CurrentUser,
    job_id: uuid.UUID,
    which: Literal["view", "execute", "override"],
    for_update: bool = False,
) -> tuple[Job, WorkflowDefinition]:
    job = await engine.get_job(session, job_id, for_update=for_update)
    wf = _wf(job)
    if not _sees_all(user, wf) and job.assignee_user_id not in (None, user.id):
        raise PermissionDenied("Job này do người khác phụ trách.")
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
    workflows: str = Query("", max_length=500, description="comma-separated workflow keys"),
    assignee: Literal["", "me"] = "",
    status: str = "",
    subject_key: str = "",
    q: str = Query("", max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    lean: bool = False,
) -> dict[str, Any]:
    allowed = [w.key for _, w in get_registry().workflows() if user.has(w.view_permission)]
    if workflow and workflow not in allowed:
        raise PermissionDenied("Bạn không có quyền xem quy trình này.")
    keys = (
        [workflow]
        if workflow
        else [k for k in workflows.split(",") if k in allowed]
        if workflows
        else allowed
    )
    # Workflows whose other people's jobs I may not see: mine and the unassigned ones only.
    scoped = {w.key for _, w in get_registry().workflows() if w.key in keys and not _sees_all(user, w)}
    visible = or_(
        Job.workflow_key.notin_(scoped) if scoped else Job.workflow_key.in_(keys),
        Job.assignee_user_id == user.id,
        Job.assignee_user_id.is_(None),
    )
    stmt = select(Job).where(Job.workflow_key.in_(keys), visible)
    if assignee == "me":
        stmt = stmt.where(Job.assignee_user_id == user.id)
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
                select(Job.status, func.count())
                .where(Job.workflow_key.in_(keys), visible)
                .group_by(Job.status)
            )
        ).all()
    )
    if lean:
        stmt = stmt.options(raiseload(Job.evidence), raiseload(Job.assignments))
    rows = (await session.scalars(stmt.order_by(Job.updated_at.desc()).offset(offset).limit(limit))).all()
    return {"total": total, "counts": counts, "items": [job_out(j, lean=lean) for j in rows]}


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


@router.post("/jobs/{job_id}/reopen")
async def reopen(
    job_id: uuid.UUID,
    body: ReasonIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    await engine.reopen_job(session, user.actor, job, body.reason)
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


# ------------------------------------------------------------------ verification & evidence
class VerifyIn(BaseModel):
    ok: bool = True
    checked: list[str] = Field(default_factory=list, max_length=50)
    note: str = Field("", max_length=1000)


@router.post("/jobs/{job_id}/steps/{key}/verify")
async def verify(
    job_id: uuid.UUID,
    key: str,
    body: VerifyIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    await engine.verify_step(session, user.actor, job, key, body.ok, body.checked, body.note)
    return await _done(session, user, job, wf)


async def _evidence_step(
    session: AsyncSession, user: CurrentUser, job_id: uuid.UUID, key: str
) -> tuple[Job, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    step = engine._find_step(job, key)
    cl.ensure_evidence_open(job, step)
    return job, wf


async def _evidence_added(session: AsyncSession, user: CurrentUser, job: Job, ev: JobEvidence) -> None:
    job.evidence.append(ev)
    engine.touch(job)
    engine.event(
        session, job, f"Thêm bằng chứng ({ev.filename or ev.kind}).", step=ev.step_key, actor=user.upn
    )
    await record(
        session,
        actor=user.actor,
        module_id=job.module_id,
        action="workflow.evidence_added",
        target_type="job",
        target_id=str(job.id),
        target_label=f"{job.subject_label} – {ev.step_key}",
        after={"kind": ev.kind, "filename": ev.filename, "url": ev.url},
    )


@router.post("/jobs/{job_id}/steps/{key}/evidence/file", status_code=201)
async def add_evidence_file(
    job_id: uuid.UUID,
    key: str,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _evidence_step(session, user, job_id, key)
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    check_size(data)
    mime = sniff(data, set(cl.EVIDENCE_MIME))
    name = safe_filename(file.filename or "bang-chung")
    stored = await save_file(session, data, name, mime, user.upn)
    ev = JobEvidence(
        job_id=job.id,
        step_key=key,
        kind=cl.EVIDENCE_MIME[mime],
        file_id=stored.id,
        filename=name,
        created_by=user.upn,
    )
    await _evidence_added(session, user, job, ev)
    return await _done(session, user, job, wf)


class EvidenceIn(BaseModel):
    kind: Literal["link", "note"]
    url: str = Field("", max_length=1000)
    text: str = Field("", max_length=2000)


@router.post("/jobs/{job_id}/steps/{key}/evidence", status_code=201)
async def add_evidence(
    job_id: uuid.UUID,
    key: str,
    body: EvidenceIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _evidence_step(session, user, job_id, key)
    if body.kind == "link":
        ev = JobEvidence(
            job_id=job.id,
            step_key=key,
            kind="link",
            url=cl.validate_link(body.url),
            text=body.text.strip() or None,
            created_by=user.upn,
        )
    else:
        if len(body.text.strip()) < 3:
            raise AppError("Ghi chú quá ngắn.", code="note_too_short")
        ev = JobEvidence(
            job_id=job.id, step_key=key, kind="note", text=body.text.strip(), created_by=user.upn
        )
    await _evidence_added(session, user, job, ev)
    return await _done(session, user, job, wf)


@router.delete("/jobs/{job_id}/evidence/{evidence_id}")
async def delete_evidence(
    job_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, wf = await _load(session, user, job_id, "execute", for_update=True)
    ev = cl.find_evidence(job, evidence_id)
    cl.ensure_evidence_open(job, engine._find_step(job, ev.step_key))
    job.evidence.remove(ev)
    engine.touch(job)
    engine.event(
        session, job, f"Xoá bằng chứng ({ev.filename or ev.kind}).", step=ev.step_key, actor=user.upn
    )
    await record(
        session,
        actor=user.actor,
        module_id=job.module_id,
        action="workflow.evidence_removed",
        target_type="job",
        target_id=str(job.id),
        target_label=f"{job.subject_label} – {ev.step_key}",
        before={"kind": ev.kind, "filename": ev.filename, "url": ev.url},
    )
    return await _done(session, user, job, wf)


@router.get("/jobs/{job_id}/evidence/{evidence_id}/file")
async def evidence_file(
    job_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    job, _ = await _load(session, user, job_id, "view")
    ev = cl.find_evidence(job, evidence_id)
    if ev.file_id is None:
        raise AppError("Bằng chứng này không có file.", code="no_file")
    stored, data = await read_file(session, ev.file_id)
    return Response(
        data,
        media_type=stored.mime,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(ev.filename or 'file')}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
            "Cache-Control": "private, no-store",
        },
    )


# ------------------------------------------------------------------ flowchart & checklists
def _visible(user: CurrentUser, module: str = "") -> list[tuple[str, WorkflowDefinition]]:
    return [
        (m, w)
        for m, w in get_registry().workflows()
        if user.has(w.view_permission) and (not module or m == module)
    ]


async def _workflow_out(session: AsyncSession, module_id: str, wf: WorkflowDefinition) -> dict[str, Any]:
    version, steps = await cl.current_checklist(session, wf)
    history = (
        await session.scalars(
            select(WorkflowChecklist)
            .where(WorkflowChecklist.workflow_key == wf.key)
            .order_by(WorkflowChecklist.version.desc())
            .limit(20)
        )
    ).all()
    return {
        "key": wf.key,
        "module_id": module_id,
        "title": wf.title,
        "verify_steps": wf.verify_steps,
        "checklist_version": version,
        "required_for_close": list(wf.required_for_close),
        "steps": [
            {
                "key": s.key,
                "title": s.title,
                "kind": s.kind,
                "description": s.description,
                "writes": list(s.writes),
                "confirm": s.confirm,
                "skippable": s.skippable,
                "waits": s.wait_timeout_seconds is not None,
                "checklist": steps[s.key],
            }
            for s in wf.steps
        ],
        "history": [
            {"version": h.version, "note": h.note, "created_by": h.created_by, "created_at": h.created_at}
            for h in history
        ],
    }


@router.get("/workflows")
async def list_workflows(
    module: str = Query("", max_length=64),
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The workflows I may run (or whose checklists I manage); the checklist history only for checklist managers."""
    manager = user.has(CHECKLIST_MANAGE)
    out = []
    for m, w in _visible(user, module):
        if manager or user.has(w.execute_permission):
            wf = await _workflow_out(session, m, w)
            out.append(wf if manager else {**wf, "history": []})
    return out


class ChecklistIn(BaseModel):
    steps: dict[str, Any]
    note: str = Field("", max_length=500)


@router.put("/workflows/{key}/checklist")
async def put_checklist(
    key: str,
    body: ChecklistIn,
    user: CurrentUser = Depends(require(CHECKLIST_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    module_id, wf = engine.workflow_or_404(key)
    if not user.has(wf.view_permission):
        raise PermissionDenied("Bạn không có quyền xem quy trình này.")
    if not wf.verify_steps:
        raise AppError("Quy trình này không dùng checklist.", code="no_verification")
    await cl.save_checklist(session, user.actor, module_id, wf, body.steps, body.note)
    await session.commit()
    return await _workflow_out(session, module_id, wf)
