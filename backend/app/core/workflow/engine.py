"""Generic workflow engine.

Jobs advance step by step in the worker (``core.workflow`` scheduled task). API actions only change
state (start, confirm, complete, retry, skip, cancel) and ask the worker to run soon, so calls to
external systems never happen inside an HTTP request. Each job is advanced under a row lock
(``FOR UPDATE SKIP LOCKED``) – two workers never run the same job.
"""

from __future__ import annotations

import traceback
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor, record
from app.core.errors import AppError, Conflict, NotFound, PermissionDenied
from app.core.logging import get_logger
from app.core.modules.registry import get_registry
from app.core.workflow.checklist import check_verification, snapshot, verifies
from app.core.workflow.definitions import StepContext, StepDefinition, StepOutcome, WorkflowDefinition
from app.core.workflow.models import Job, JobAssignment, JobEvent, JobStep

log = get_logger(__name__)
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PUMP_TASK_KEY = "core.workflow"
ACTIVE = ("running", "waiting")
FINISHED_STEP = ("done", "skipped")


def now() -> datetime:
    return datetime.now(UTC)


def today_vn() -> date:
    return datetime.now(TZ).date()


def workflow_or_404(key: str) -> tuple[str, WorkflowDefinition]:
    found = get_registry().workflow(key)
    if found is None:
        raise NotFound("Quy trình không tồn tại hoặc module đang tắt.")
    return found


def step_def(wf: WorkflowDefinition, key: str) -> StepDefinition:
    for s in wf.steps:
        if s.key == key:
            return s
    raise NotFound(f"Bước {key} không còn trong quy trình.")


def event(
    session: AsyncSession,
    job: Job,
    message: str,
    *,
    step: str | None = None,
    level: str = "info",
    actor: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    session.add(
        JobEvent(job_id=job.id, step_key=step, level=level, message=message[:4000], actor=actor, data=data)
    )


def outputs_of(job: Job) -> dict[str, dict[str, Any]]:
    return {s.key: s.output or {} for s in job.steps}


# ------------------------------------------------------------------ creation
async def create_job(
    session: AsyncSession,
    actor: Actor,
    workflow_key: str,
    *,
    params: dict[str, Any],
    subject_key: str,
    subject_label: str,
    external_ref: str | None,
    idempotency_key: str,
) -> tuple[Job, bool]:
    """Create a draft job, or return the existing one for the same idempotency key."""
    module_id, wf = workflow_or_404(workflow_key)
    existing = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        return existing, False
    job = Job(
        workflow_key=wf.key,
        module_id=module_id,
        status="draft",
        subject_key=subject_key,
        subject_label=subject_label[:256],
        external_ref=external_ref,
        idempotency_key=idempotency_key,
        params=params,
        created_by=actor.upn or "system",
        steps=[],
        evidence=[],
        assignments=[],
    )
    seq = 0
    for s in wf.steps:
        if s.when(params):
            seq += 1
            job.steps.append(
                JobStep(
                    seq=seq,
                    key=s.key,
                    title=s.title,
                    kind=s.kind,
                    status="pending",
                    requires_confirm=s.confirm,
                    output={},
                )
            )
    session.add(job)
    await session.flush()
    event(session, job, "Đã tạo Job ở trạng thái nháp.", actor=actor.upn)
    await record(
        session,
        actor=actor,
        module_id=module_id,
        action="workflow.job_created",
        target_type="job",
        target_id=str(job.id),
        target_label=f"{wf.title} – {subject_label}",
        after={"workflow": wf.key, "subject": subject_key, "external_ref": external_ref},
    )
    return job, True


async def get_job(session: AsyncSession, job_id: Any, *, for_update: bool = False) -> Job:
    # populate_existing: a locked re-read must not keep stale attributes from the identity map.
    job = await session.get(Job, job_id, with_for_update=for_update, populate_existing=for_update)
    if job is None:
        raise NotFound("Không tìm thấy Job.")
    return job


def need(user_perms: set[str], code: str) -> None:
    if code not in user_perms:
        raise PermissionDenied("Bạn không có quyền thực hiện thao tác này.", details={"missing": [code]})


def check_version(job: Job, version: int | None) -> None:
    if version is not None and version != job.version:
        raise Conflict(
            "Job vừa được cập nhật bởi người khác hoặc bởi hệ thống. Tải lại trang.", code="stale_version"
        )


def touch(job: Job) -> None:
    job.version += 1


# ------------------------------------------------------------------ user actions
async def start_job(session: AsyncSession, actor: Actor, job: Job, scheduled_for: date | None) -> Job:
    if job.status not in ("draft", "scheduled"):
        raise Conflict("Job đã được bắt đầu.", code="already_started")
    _, wf = workflow_or_404(job.workflow_key)
    if wf.verify_steps:
        await snapshot(session, job, wf)
    job.started_by = actor.upn
    if scheduled_for and scheduled_for > today_vn():
        job.status = "scheduled"
        job.scheduled_for = scheduled_for
        msg = f"Đã lên lịch chạy ngày {scheduled_for:%d/%m/%Y}."
    else:
        job.status = "running"
        job.started_at = now()
        job.scheduled_for = None
        msg = "Đã bắt đầu Job."
    touch(job)
    event(session, job, msg, actor=actor.upn)
    await record(
        session,
        actor=actor,
        module_id=job.module_id,
        action="workflow.job_started",
        target_type="job",
        target_id=str(job.id),
        target_label=job.subject_label,
        after={
            "status": job.status,
            "scheduled_for": job.scheduled_for.isoformat() if job.scheduled_for else None,
        },
    )
    return job


def _find_step(job: Job, key: str) -> JobStep:
    for s in job.steps:
        if s.key == key:
            return s
    raise NotFound("Không tìm thấy bước.")


def _resume(job: Job) -> None:
    if job.status in ("waiting", "failed"):
        job.status = "running"


async def confirm_step(session: AsyncSession, actor: Actor, job: Job, key: str, typed: str) -> JobStep:
    step = _find_step(job, key)
    if not step.requires_confirm:
        raise AppError("Bước này không cần xác nhận.", code="no_confirm")
    if typed.strip() != job.subject_key:
        raise AppError("Mã xác nhận không khớp.", code="confirm_mismatch")
    if step.status in FINISHED_STEP:
        raise Conflict("Bước đã hoàn tất.", code="step_finished")
    step.confirmed_by = actor.upn
    step.confirmed_at = now()
    step.next_run_at = now()
    if step.status == "waiting":
        step.status = "pending"
    _resume(job)
    touch(job)
    event(session, job, f"Đã xác nhận chạy bước “{step.title}”.", step=key, actor=actor.upn)
    await record(
        session,
        actor=actor,
        module_id=job.module_id,
        action="workflow.step_confirmed",
        target_type="job",
        target_id=str(job.id),
        target_label=f"{job.subject_label} – {step.title}",
    )
    return step


async def complete_manual(
    session: AsyncSession, actor: Actor, job: Job, key: str, data: dict[str, Any]
) -> JobStep:
    _, wf = workflow_or_404(job.workflow_key)
    step = _find_step(job, key)
    definition = step_def(wf, key)
    if step.kind != "manual":
        raise AppError("Bước tự động không xác nhận thủ công được.", code="not_manual")
    if step.status in FINISHED_STEP:
        raise Conflict("Bước đã hoàn tất.", code="step_finished")
    if job.status not in ACTIVE:
        raise Conflict("Job chưa chạy hoặc đã kết thúc.", code="job_not_active")
    if _blocked_by_earlier(job, step):
        raise Conflict("Hoàn tất các bước trước đó trước.", code="earlier_steps_open")
    ctx = StepContext(session=session, job=job, step=step, actor=actor, outputs=outputs_of(job), input=data)
    try:
        outcome = await definition.run(ctx) if definition.run else StepOutcome("done", "Đã xác nhận.")
    except AppError as exc:
        raise AppError(exc.message, code="manual_incomplete", details=exc.details) from exc
    if outcome.status != "done":
        raise AppError(outcome.message or "Chưa hoàn tất được bước này.", code="manual_incomplete")
    _apply(step, outcome, actor.upn)
    if verifies(job):
        step.status = "verify"
    _resume(job)
    touch(job)
    event(session, job, outcome.message or f"Đã hoàn tất “{step.title}”.", step=key, actor=actor.upn)
    await record(
        session,
        actor=actor,
        module_id=job.module_id,
        action="workflow.step_completed",
        target_type="job",
        target_id=str(job.id),
        target_label=f"{job.subject_label} – {step.title}",
        after=outcome.output or None,
    )
    return step


async def set_assignee(
    session: AsyncSession,
    job: Job,
    *,
    user_id: Any,
    name: str,
    actor: str,
    source: str,
    reason: str = "",
) -> bool:
    """Record who is responsible now. No-op when unchanged. Returns whether it changed."""
    if job.assignee_name == name and job.assignee_user_id == user_id:
        return False
    before = job.assignee_name
    job.assignments.append(
        JobAssignment(
            from_name=before,
            to_name=name[:256],
            to_user_id=user_id,
            step_key=job.current_step if job.status not in ("draft", "scheduled") else None,
            source=source,
            actor=actor[:320],
            reason=reason[:500],
        )
    )
    job.assignee_user_id, job.assignee_name = user_id, name[:256]
    touch(job)
    via = "trên SDP" if source == "sdp" else "trong infra-ops"
    event(
        session,
        job,
        f"Giao cho {name}"
        if before is None
        else f"Chuyển từ {before} sang {name} ({via})" + (f": {reason}" if reason else ""),
        actor=actor,
    )
    await record(
        session,
        actor=Actor(None, actor),
        module_id=job.module_id,
        action="workflow.job_assigned",
        target_type="job",
        target_id=str(job.id),
        target_label=job.subject_label,
        before={"assignee": before},
        after={"assignee": name, "source": source},
        message=reason or None,
    )
    await session.flush()
    return True


async def verify_step(
    session: AsyncSession, actor: Actor, job: Job, key: str, ok: bool, checked: list[str], note: str
) -> JobStep:
    """Operator check of a performed step: confirm (checklist + evidence) or reject (step fails)."""
    step = _find_step(job, key)
    if step.status != "verify":
        raise Conflict("Bước không ở trạng thái chờ kiểm tra.", code="not_verifying")
    if job.status not in ACTIVE:
        raise Conflict("Job chưa chạy hoặc đã kết thúc.", code="job_not_active")
    note = note.strip()
    label = f"{job.subject_label} – {step.title}"
    if not ok:
        if len(note) < 3:
            raise AppError("Ghi rõ vì sao kiểm tra không đạt.", code="reason_required")
        step.status = "failed"
        step.message = f"Kiểm tra không đạt: {note}"[:1000]
        job.status = "failed"
        touch(job)
        event(
            session,
            job,
            f"Kiểm tra “{step.title}” không đạt: {note}",
            step=key,
            level="error",
            actor=actor.upn,
        )
        await record(
            session,
            actor=actor,
            module_id=job.module_id,
            action="workflow.step_rejected",
            result="failure",
            target_type="job",
            target_id=str(job.id),
            target_label=label,
            message=note[:1000],
        )
        return step
    step.checklist = check_verification(job, step, checked)
    step.status = "done"
    step.verified_by = actor.upn
    step.verified_at = now()
    step.verify_note = note[:1000] or None
    _resume(job)
    touch(job)
    event(session, job, f"Đã kiểm tra và xác nhận “{step.title}”.", step=key, actor=actor.upn)
    await record(
        session,
        actor=actor,
        module_id=job.module_id,
        action="workflow.step_verified",
        target_type="job",
        target_id=str(job.id),
        target_label=label,
        after={
            "checked": [i["text"] for i in step.checklist],
            "evidence": [e.filename or e.url or e.kind for e in job.evidence if e.step_key == key],
            "note": note or None,
        },
    )
    return step


async def retry_step(session: AsyncSession, actor: Actor, job: Job, key: str) -> JobStep:
    step = _find_step(job, key)
    if step.status != "failed":
        raise Conflict("Chỉ chạy lại bước đang lỗi.", code="not_failed")
    step.status = "pending"
    step.next_run_at = now()
    step.waiting_since = None
    _resume(job)
    touch(job)
    event(session, job, f"Chạy lại bước “{step.title}”.", step=key, actor=actor.upn)
    await record(
        session,
        actor=actor,
        module_id=job.module_id,
        action="workflow.step_retried",
        target_type="job",
        target_id=str(job.id),
        target_label=f"{job.subject_label} – {step.title}",
    )
    return step


async def skip_step(session: AsyncSession, actor: Actor, job: Job, key: str, reason: str) -> JobStep:
    _, wf = workflow_or_404(job.workflow_key)
    step = _find_step(job, key)
    if not step_def(wf, key).skippable:
        raise AppError("Bước này không được bỏ qua.", code="not_skippable")
    if step.status in FINISHED_STEP:
        raise Conflict("Bước đã hoàn tất.", code="step_finished")
    if step.status == "verify":
        raise Conflict(
            "Bước đã thực hiện, đang chờ kiểm tra – hãy xác nhận hoặc báo không đạt.", code="step_verifying"
        )
    step.status = "skipped"
    step.finished_at = now()
    step.actor = actor.upn
    step.message = f"Bỏ qua: {reason}"
    _resume(job)
    touch(job)
    event(session, job, f"Bỏ qua bước “{step.title}”: {reason}", step=key, level="warn", actor=actor.upn)
    await record(
        session,
        actor=actor,
        module_id=job.module_id,
        action="workflow.step_skipped",
        target_type="job",
        target_id=str(job.id),
        target_label=f"{job.subject_label} – {step.title}",
        after={"reason": reason},
    )
    return step


async def cancel_job(session: AsyncSession, actor: Actor, job: Job, reason: str) -> Job:
    if job.status in ("completed", "cancelled"):
        raise Conflict("Job đã kết thúc.", code="job_finished")
    before = job.status
    job.status = "cancelled"
    job.closed_at = now()
    job.close_reason = reason[:500]
    touch(job)
    event(session, job, f"Đã huỷ Job: {reason}", level="warn", actor=actor.upn)
    await record(
        session,
        actor=actor,
        module_id=job.module_id,
        action="workflow.job_cancelled",
        target_type="job",
        target_id=str(job.id),
        target_label=job.subject_label,
        before={"status": before},
        after={"reason": reason},
    )
    return job


async def override_close(session: AsyncSession, actor: Actor, job: Job, reason: str) -> Job:
    """Admin: close a job although required steps (e.g. signed record) are missing."""
    if job.status in ("completed", "cancelled"):
        raise Conflict("Job đã kết thúc.", code="job_finished")
    before = job.status
    open_steps = [s.title for s in job.steps if s.status not in FINISHED_STEP]
    job.status = "completed"
    job.closed_at = now()
    job.close_reason = f"Đóng bắt buộc: {reason}"[:500]
    touch(job)
    event(
        session,
        job,
        f"Đóng Job bắt buộc: {reason}. Bước chưa xong: {', '.join(open_steps) or 'không'}",
        level="warn",
        actor=actor.upn,
    )
    await record(
        session,
        actor=actor,
        module_id=job.module_id,
        action="workflow.job_override_closed",
        target_type="job",
        target_id=str(job.id),
        target_label=job.subject_label,
        before={"status": before, "open_steps": open_steps},
        after={"reason": reason},
    )
    return job


async def preview(session: AsyncSession, actor: Actor, job: Job) -> list[dict[str, Any]]:
    """Dry run: what every remaining step would do. Read-only."""
    _, wf = workflow_or_404(job.workflow_key)
    out = []
    outputs = outputs_of(job)
    for step in job.steps:
        d = step_def(wf, step.key)
        lines: list[str] = []
        error = None
        if step.status in FINISHED_STEP:
            lines = [step.message or "Đã hoàn tất."]
        elif d.preview is not None:
            try:
                lines = await d.preview(
                    StepContext(session=session, job=job, step=step, actor=actor, outputs=outputs)
                )
            except AppError as exc:
                error = exc.message
            except Exception as exc:  # preview must never break the page
                log.warning("workflow_preview_failed", step=step.key, error=str(exc))
                error = f"Không xem trước được: {type(exc).__name__}"
        else:
            lines = [d.description] if d.description else []
        out.append(
            {
                "key": step.key,
                "title": step.title,
                "status": step.status,
                "writes": list(d.writes),
                "lines": lines,
                "error": error,
            }
        )
    return out


# ------------------------------------------------------------------ worker
def _apply(step: JobStep, outcome: StepOutcome, actor: str | None) -> None:
    step.message = outcome.message
    if outcome.output:
        step.output = {**(step.output or {}), **outcome.output}
    _merge_before(step, outcome.before_state)
    if outcome.status in ("done", "skipped"):
        step.status = outcome.status
        step.finished_at = now()
        step.actor = actor
        step.next_run_at = None
        step.waiting_since = None


def _merge_before(step: JobStep, before: dict[str, Any] | None) -> None:
    """Keep the first-seen state across retries: existing keys win, nested dicts merged one level deep."""
    if before is None:
        return
    old = step.before_state or {}
    merged = {**before, **old}
    for k, v in before.items():
        if isinstance(v, dict) and isinstance(old.get(k), dict):
            merged[k] = {**v, **old[k]}
    step.before_state = merged


def _blocked_by_earlier(job: Job, step: JobStep) -> bool:
    return any(s.seq < step.seq and s.status not in FINISHED_STEP for s in job.steps)


async def advance(session: AsyncSession, job: Job, worker_actor: Actor) -> None:
    """Run as many steps as possible; stop at a manual step, a wait, a confirmation or a failure."""
    _, wf = workflow_or_404(job.workflow_key)
    for step in sorted(job.steps, key=lambda s: s.seq):
        if step.status in FINISHED_STEP:
            continue
        job.current_step = step.key
        definition = step_def(wf, step.key)
        ctx = StepContext(session=session, job=job, step=step, actor=worker_actor, outputs=outputs_of(job))
        if step.status == "failed":
            job.status = "failed"
            return
        if step.status == "verify":
            job.status = "waiting"
            return
        if step.kind == "manual":
            if step.status != "waiting":
                step.status = "waiting"
                step.waiting_since = now()
                if definition.prepare is not None:
                    try:
                        prep = await definition.prepare(ctx)
                        _apply(step, StepOutcome("waiting", prep.message, prep.output), None)
                    except Exception as exc:
                        step.message = f"Không chuẩn bị được dữ liệu: {getattr(exc, 'message', str(exc))}"
                event(session, job, f"Chờ xác nhận thủ công: “{step.title}”.", step=step.key)
            job.status = "waiting"
            return
        if step.requires_confirm and step.confirmed_at is None:
            if step.status != "waiting":
                step.status = "waiting"
                step.waiting_since = now()
                step.message = definition.confirm_text or "Chờ xác nhận bằng Mã NV trước khi chạy."
                event(session, job, f"Chờ xác nhận trước khi chạy “{step.title}”.", step=step.key)
            job.status = "waiting"
            return
        if step.next_run_at and step.next_run_at > now():
            job.status = "waiting"
            return
        # Run the automatic step.
        step.status = "in_progress"
        step.attempts += 1
        step.started_at = step.started_at or now()
        await session.flush()
        try:
            assert definition.run is not None
            outcome = await definition.run(ctx)
        except AppError as exc:
            outcome = StepOutcome("failed", exc.message)
        except Exception as exc:
            log.error(
                "workflow_step_crashed", step=step.key, error=str(exc), trace=traceback.format_exc(limit=5)
            )
            outcome = StepOutcome("failed", f"Lỗi không mong đợi: {type(exc).__name__}: {exc}"[:1000])
        if outcome.status == "waiting":
            _apply(step, outcome, None)
            step.status = "waiting"
            step.waiting_since = step.waiting_since or now()
            step.next_run_at = now() + timedelta(seconds=outcome.retry_in_seconds)
            timeout = definition.wait_timeout_seconds
            if timeout and step.waiting_since + timedelta(seconds=timeout) < now():
                step.status = "failed"
                step.message = f"Quá thời gian chờ. {outcome.message}"
                event(session, job, f"Bước “{step.title}” quá thời gian chờ.", step=step.key, level="error")
                job.status = "failed"
                return
            job.status = "waiting"
            return
        if outcome.status == "failed":
            step.status = "failed"
            step.message = outcome.message
            if outcome.output:
                step.output = {**(step.output or {}), **outcome.output}
            _merge_before(step, outcome.before_state)
            event(session, job, f"Lỗi ở bước “{step.title}”: {outcome.message}", step=step.key, level="error")
            await record(
                session,
                actor=worker_actor,
                module_id=job.module_id,
                action="workflow.step_failed",
                result="failure",
                target_type="job",
                target_id=str(job.id),
                target_label=f"{job.subject_label} – {step.title}",
                message=outcome.message[:1000],
            )
            job.status = "failed"
            return
        _apply(step, outcome, worker_actor.upn)
        event(session, job, outcome.message or f"Hoàn tất “{step.title}”.", step=step.key)
        if definition.writes:
            await record(
                session,
                actor=worker_actor,
                module_id=job.module_id,
                action="workflow.step_executed",
                system_type=",".join(definition.writes),
                target_type="job",
                target_id=str(job.id),
                target_label=f"{job.subject_label} – {step.title}",
                before=outcome.before_state,
                after=outcome.output or None,
                message=outcome.message[:1000],
            )
        if verifies(job):
            step.status = "verify"
            event(session, job, f"Chờ kiểm tra và xác nhận “{step.title}”.", step=step.key)
            job.status = "waiting"
            return
    job.status = "completed"
    job.current_step = None
    job.closed_at = now()
    event(session, job, "Job đã hoàn tất.")


async def pump(session: AsyncSession) -> dict[str, Any]:
    """Scheduled task: start due scheduled jobs and advance active ones (one transaction per job)."""
    ids = (
        await session.scalars(
            select(Job.id)
            .where(
                or_(
                    Job.status.in_(ACTIVE),
                    (Job.status == "scheduled") & (Job.scheduled_for <= today_vn()),
                )
            )
            # No LIMIT: jobs waiting on people never change, so a capped oldest-first page starves new ones.
            .order_by(Job.updated_at)
        )
    ).all()
    advanced = 0
    for job_id in ids:
        job = await session.scalar(
            select(Job)
            .where(Job.id == job_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if job is None:
            continue
        if job.status == "scheduled":
            job.status = "running"
            job.started_at = now()
            event(session, job, "Đến ngày đã lên lịch – bắt đầu Job.")
        if job.status == "waiting" and not _has_due_work(job):
            await session.commit()
            continue
        actor = Actor(user_id=None, upn=f"worker ({job.started_by or 'system'})")
        try:
            await advance(session, job, actor)
            touch(job)
            await session.commit()
            advanced += 1
        except Exception:
            await session.rollback()
            log.exception("workflow_advance_failed", job_id=str(job_id))
    return {"advanced": advanced, "candidates": len(ids)}


def _has_due_work(job: Job) -> bool:
    """A waiting job only needs the worker when an automatic step is due (confirmed or timer elapsed)."""
    for s in sorted(job.steps, key=lambda s: s.seq):
        if s.status in FINISHED_STEP:
            continue
        if s.status == "verify":
            return False
        if s.kind == "manual":
            return s.status != "waiting"
        if s.requires_confirm and s.confirmed_at is None:
            return s.status != "waiting"
        return s.next_run_at is None or s.next_run_at <= now() or s.status == "pending"
    return True
