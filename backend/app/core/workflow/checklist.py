"""Verification checklists and evidence for workflow steps.

Checklists are versioned per workflow: version 0 is the default declared in code, every save by a
manager adds a version. A job snapshots the current version when it starts, so later edits never
change the rules of a running job.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor, record
from app.core.errors import AppError, Conflict, NotFound
from app.core.workflow.definitions import WorkflowDefinition
from app.core.workflow.models import Job, JobEvidence, JobStep, WorkflowChecklist

EVIDENCE_MIME = {
    "image/png": "image",
    "image/jpeg": "image",
    "application/pdf": "file",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "file",
}
MAX_EVIDENCE_PER_STEP = 20


class ChecklistItem(BaseModel):
    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    text: str = Field(min_length=1, max_length=300)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Nội dung không được để trống.")
        return v


class StepChecklist(BaseModel):
    items: list[ChecklistItem] = Field(default_factory=list, max_length=30)
    evidence_required: bool = False


def default_steps(wf: WorkflowDefinition) -> dict[str, dict[str, Any]]:
    return {
        s.key: {
            "items": [{"id": f"c{i + 1}", "text": t} for i, t in enumerate(s.checklist)],
            "evidence_required": s.evidence_required,
        }
        for s in wf.steps
    }


async def current_checklist(session: AsyncSession, wf: WorkflowDefinition) -> tuple[int, dict[str, Any]]:
    """Latest saved version, or (0, defaults from code). Steps added in code later get their default."""
    row = await session.scalar(
        select(WorkflowChecklist)
        .where(WorkflowChecklist.workflow_key == wf.key)
        .order_by(WorkflowChecklist.version.desc())
        .limit(1)
    )
    defaults = default_steps(wf)
    if row is None:
        return 0, defaults
    return row.version, {k: row.steps.get(k, defaults[k]) for k in defaults}


async def save_checklist(
    session: AsyncSession,
    actor: Actor,
    module_id: str,
    wf: WorkflowDefinition,
    steps: dict[str, Any],
    note: str,
) -> WorkflowChecklist:
    known = {s.key for s in wf.steps}
    unknown = set(steps) - known
    if unknown:
        raise AppError(
            "Có bước không thuộc quy trình.", code="unknown_step", details={"steps": sorted(unknown)}
        )
    clean: dict[str, Any] = {}
    for key in (s.key for s in wf.steps):
        sc = StepChecklist.model_validate(steps.get(key) or {})
        ids = [i.id for i in sc.items]
        if len(ids) != len(set(ids)):
            raise AppError("Mã mục checklist bị trùng.", code="duplicate_item")
        clean[key] = sc.model_dump()
    before_version, before = await current_checklist(session, wf)
    if clean == before:
        raise AppError("Checklist không thay đổi.", code="no_change")
    latest = await session.scalar(
        select(func.max(WorkflowChecklist.version)).where(WorkflowChecklist.workflow_key == wf.key)
    )
    row = WorkflowChecklist(
        workflow_key=wf.key,
        version=(latest or 0) + 1,
        steps=clean,
        note=note.strip()[:500],
        created_by=actor.upn or "?",
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        actor=actor,
        module_id=module_id,
        action="workflow.checklist_saved",
        target_type="workflow",
        target_id=wf.key,
        target_label=wf.title,
        before={"version": before_version, "steps": before},
        after={"version": row.version, "steps": clean},
        message=row.note or None,
    )
    return row


async def snapshot(session: AsyncSession, job: Job, wf: WorkflowDefinition) -> None:
    """Freeze the current checklist onto the job's steps (called when the job starts)."""
    version, steps = await current_checklist(session, wf)
    job.checklist_version = version
    for step in job.steps:
        sc = steps.get(step.key) or {}
        step.checklist = [{"id": i["id"], "text": i["text"], "checked": False} for i in sc.get("items", [])]
        step.evidence_required = bool(sc.get("evidence_required"))


def verifies(job: Job) -> bool:
    return job.checklist_version is not None


def evidence_of(job: Job, key: str) -> list[JobEvidence]:
    return [e for e in job.evidence if e.step_key == key]


def check_verification(job: Job, step: JobStep, checked: list[str]) -> list[dict[str, Any]]:
    """Return the ticked checklist, or raise when an item or required evidence is missing."""
    items = step.checklist or []
    missing = [i["text"] for i in items if i["id"] not in set(checked)]
    if missing:
        raise AppError(
            "Chưa kiểm tra đủ các mục trong checklist.",
            code="checklist_incomplete",
            details={"missing": missing},
        )
    if step.evidence_required and not evidence_of(job, step.key):
        raise AppError("Bước này cần đính kèm bằng chứng trước khi xác nhận.", code="evidence_required")
    return [{**i, "checked": True} for i in items]


def ensure_evidence_open(job: Job, step: JobStep) -> None:
    if not verifies(job):
        raise AppError("Job này không dùng xác nhận theo checklist.", code="no_verification")
    if job.status in ("completed", "cancelled"):
        raise Conflict("Job đã kết thúc.", code="job_finished")
    if step.status in ("done", "skipped"):
        raise Conflict("Bước đã xác nhận, không thay đổi bằng chứng được nữa.", code="step_finished")
    if len(evidence_of(job, step.key)) >= MAX_EVIDENCE_PER_STEP:
        raise AppError(f"Tối đa {MAX_EVIDENCE_PER_STEP} bằng chứng mỗi bước.", code="too_much_evidence")


_URL = re.compile(r"^https?://\S{3,}$", re.IGNORECASE)


def validate_link(url: str) -> str:
    url = url.strip()
    if not _URL.match(url):
        raise AppError("Liên kết phải bắt đầu bằng http:// hoặc https://.", code="invalid_link")
    return url


def find_evidence(job: Job, evidence_id: uuid.UUID) -> JobEvidence:
    for e in job.evidence:
        if e.id == evidence_id:
            return e
    raise NotFound("Không tìm thấy bằng chứng.")


def evidence_out(e: JobEvidence) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "step_key": e.step_key,
        "kind": e.kind,
        "filename": e.filename,
        "url": e.url,
        "text": e.text,
        "created_by": e.created_by,
        "created_at": e.created_at,
    }
