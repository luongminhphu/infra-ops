"""Playbook inside a Job: the guide mapped to the Job's workflow, run for the Job's employee. Manual steps are
ticked, branches chosen and check decisions frozen per Job (saved + audited). Reading never writes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.db import get_session
from app.core.errors import AppError, Conflict, NotFound
from app.core.modules.registry import get_registry
from app.core.settings.service import save_settings
from app.core.workflow import engine
from app.core.workflow.api import check_job_scope
from app.core.workflow.definitions import WorkflowDefinition
from app.core.workflow.models import Job
from app.modules.playbook.api import MODULE_ID, PUBLISH, VIEW
from app.modules.playbook.models import Guide, GuideVersion, JobMark, JobRun
from app.modules.playbook.run import _is_choice, run_for_job
from app.modules.playbook.settings import SETTINGS_KEY, JobGuides
from app.modules.playbook.settings import load as load_guides
from app.modules.playbook.tree import Tree, job_refs

router = APIRouter(tags=["playbook"])
view = require(VIEW)
publish_perm = require(PUBLISH)
Pair = tuple[Guide, GuideVersion]
LOCK_TIMEOUT = (
    "3s"  # how long a write waits for the Job row while the worker runs a step (module constant: tests)
)


def _json_safe(v: Any) -> Any:
    """Fact values are stored as JSON: numbers, strings, bools and None stay, anything else becomes a string."""
    return v if v is None or isinstance(v, (bool, int, float, str)) else str(v)


def _frozen(d: dict[str, Any], now: str) -> dict[str, Any]:
    side = lambda o: o if o is None else o | {"value": _json_safe(o.get("value"))}  # noqa: E731
    return d | {"left": side(d.get("left")), "right": side(d.get("right")), "frozen_at": now}


def _is_lock_timeout(e: DBAPIError) -> bool:
    o = e.orig
    return "55P03" in {getattr(o, "sqlstate", None), getattr(getattr(o, "__cause__", None), "sqlstate", None)}


def employee_id(job: Job, wf: WorkflowDefinition) -> str | None:
    """The Job's subject as an employee ID (zero-filled like the Playbook run), only when the workflow says so."""
    if not wf.subject_is_employee:
        return None
    k = job.subject_key
    return k.zfill(6) if k.isdigit() and len(k) < 6 else k


# ------------------------------------------------------------------ settings
@router.get("/settings/job-guides")
async def get_job_guides(
    _: CurrentUser = Depends(publish_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    s = await load_guides(session)
    return s.model_dump(mode="json") | {
        "workflows": [{"key": w.key, "title": w.title} for _, w in get_registry().workflows()]
    }


@router.put("/settings/job-guides")
async def put_job_guides(
    body: JobGuides, user: CurrentUser = Depends(publish_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    known = {w.key for _, w in get_registry().workflows()}
    for key, gid in body.workflow_guides.items():
        if key not in known:
            raise AppError(f"Quy trình “{key}” không tồn tại.", code="unknown_workflow")
        g = await session.get(Guide, gid)
        if g is None or g.published_version is None:
            raise AppError("Chỉ gán được Playbook đã xuất bản.", code="guide_not_published")
    saved = await save_settings(
        session, user.actor, MODULE_ID, SETTINGS_KEY, JobGuides, body.model_dump(mode="json")
    )
    await session.commit()
    return saved.model_dump(mode="json")


# ------------------------------------------------------------------ resolving
async def _published(session: AsyncSession, guide_id: uuid.UUID | None = None) -> list[Pair]:
    stmt = (
        select(Guide, GuideVersion)
        .join(
            GuideVersion,
            (GuideVersion.guide_id == Guide.id) & (GuideVersion.version == Guide.published_version),
        )
        .order_by(Guide.title)
    )
    if guide_id:
        stmt = stmt.where(Guide.id == guide_id)
    return [(g, v) for g, v in (await session.execute(stmt)).all()]


async def resolve(session: AsyncSession, workflow_key: str) -> tuple[Pair | None, list[Pair]]:
    """The assigned guide, else the only published guide with a Job step of this workflow; (None, several) when
    more than one qualifies. A setting pointing to a deleted / unpublished guide is ignored."""
    if gid := (await load_guides(session)).workflow_guides.get(workflow_key):
        if rows := await _published(session, gid):
            return rows[0], []
    hits = [
        (g, v)
        for g, v in await _published(session)
        if any(ref.workflow == workflow_key for _, ref in job_refs(Tree.model_validate(v.tree)))
    ]
    return (hits[0], []) if len(hits) == 1 else (None, hits if hits else [])


# ------------------------------------------------------------------ reading
async def _run_and_payload(
    session: AsyncSession,
    user: CurrentUser,
    job: Job,
    wf: WorkflowDefinition,
    pair: Pair,
    run: JobRun | None,
    *,
    freeze: bool = False,
    pinned: bool,
) -> dict[str, Any]:
    g, v = pair
    tree = Tree.model_validate(v.tree)
    marks = {}
    if run:
        rows = await session.scalars(select(JobMark).where(JobMark.job_id == job.id))
        marks = {
            m.node_id: {"status": m.status, "reason": m.reason, "by": m.done_by, "at": m.done_at}
            for m in rows
        }
    registry = get_registry().facts()
    emp = employee_id(job, wf)

    async def load(key: str, param: str) -> Any:
        src = registry.get(key)
        return None if src is None else await src.load(session, emp or "", param)

    async def walk() -> dict[str, Any]:
        return await run_for_job(
            tree,
            load if emp else None,
            choices=run.choices if run else {},
            decisions=run.decisions if run else {},
            marks=marks,
            job_steps={s.key: {"status": s.status, "title": s.title} for s in job.steps},
            workflow=job.workflow_key,
            labels={k: f.label for k, f in registry.items()},
        )

    result = await walk()
    new = result.pop("new_decisions")
    if freeze and run and new:
        now = datetime.now(UTC).isoformat()
        run.decisions = run.decisions | {k: _frozen(d, now) for k, d in new.items()}
        result = await walk()  # the response shows what was stored (frozen_at set)
        result.pop("new_decisions")
    return {
        "guide": {"id": str(g.id), "title": g.title, "version": v.version},
        "pinned": pinned,
        "run": result,
        "can_edit": wf.execute_permission in user.permissions and job.status != "cancelled",
        "employee_id": emp,
    }


async def _view(session: AsyncSession, user: CurrentUser, job: Job, wf: WorkflowDefinition) -> dict[str, Any]:
    run = await session.get(JobRun, job.id)
    if run:
        g = await session.get(Guide, run.guide_id)
        v = await session.scalar(
            select(GuideVersion).where(
                GuideVersion.guide_id == run.guide_id, GuideVersion.version == run.version
            )
        )
        assert g is not None and v is not None  # the run's FK cascades; versions are immutable
        return await _run_and_payload(session, user, job, wf, (g, v), run, pinned=True)
    found, several = await resolve(session, job.workflow_key)
    if found is None:
        out: dict[str, Any] = {"guide": None}
        if several and user.has(PUBLISH):
            out["ambiguous"] = [{"id": str(g.id), "title": g.title, "version": v.version} for g, v in several]
        return out
    return await _run_and_payload(session, user, job, wf, found, None, pinned=False)


@router.get("/jobs/{job_id}")
async def get_job_playbook(
    job_id: uuid.UUID, user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    job = await engine.get_job(session, job_id)
    wf = engine.workflow_or_404(job.workflow_key)[1]
    check_job_scope(user, job)
    engine.need(user.permissions, wf.view_permission)
    return await _view(session, user, job, wf)


# ------------------------------------------------------------------ writing
async def _begin(
    session: AsyncSession, user: CurrentUser, job_id: uuid.UUID, guide_id: uuid.UUID, version: int,
    *, pin: bool = True,
) -> tuple[Job, WorkflowDefinition, JobRun | None, Tree, Pair]:  # fmt: skip
    """Lock the Job, check who may write, pin the shown guide on the first write (or require the pinned one).
    With pin=False (undoing something) an unpinned Job stays unpinned: run is None."""
    await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    try:
        job = await engine.get_job(session, job_id, for_update=True)
    except DBAPIError as e:
        if not _is_lock_timeout(e):
            raise
        raise Conflict("Job đang chạy một bước – thử lại sau ít giây.", code="job_busy") from e
    # Only the Job row wait is bounded: later waits (e.g. the global audit lock) behave like every other endpoint.
    await session.execute(text("SET LOCAL lock_timeout = 0"))
    wf = engine.workflow_or_404(job.workflow_key)[1]
    check_job_scope(user, job)
    engine.need(user.permissions, wf.view_permission)
    engine.need(user.permissions, wf.execute_permission)
    if job.status == "cancelled":
        raise Conflict("Job đã huỷ – không cập nhật Playbook được.", code="job_cancelled")
    stale = Conflict("Playbook vừa đổi, tải lại trang.", code="playbook_changed")
    run = await session.get(JobRun, job.id)
    if run is None:
        found, _ = await resolve(session, job.workflow_key)
        if found is None or (found[0].id, found[1].version) != (guide_id, version):
            raise stale
        pair = found
        if pin:
            run = JobRun(job_id=job.id, guide_id=guide_id, version=version, choices={}, decisions={},
                         pinned_at=datetime.now(UTC), pinned_by=user.upn)  # fmt: skip
            session.add(run)
            try:
                await session.flush()
            except IntegrityError as e:  # the guide was deleted between resolving and pinning (C-20)
                raise stale from e
            await _audit(session, user, job, "playbook.job_pinned", {"guide_id": None, "version": None},
                         {"guide_id": str(guide_id), "version": version})  # fmt: skip
    else:
        if (run.guide_id, run.version) != (guide_id, version):
            raise stale
        g = await session.get(Guide, run.guide_id)
        v = await session.scalar(
            select(GuideVersion).where(
                GuideVersion.guide_id == run.guide_id, GuideVersion.version == run.version
            )
        )
        assert g is not None and v is not None
        pair = (g, v)
    return job, wf, run, Tree.model_validate(pair[1].tree), pair


async def _audit(
    session: AsyncSession,
    user: CurrentUser,
    job: Job,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    await record(session, actor=user.actor, module_id=MODULE_ID, action=action, target_type="job",
                 target_id=str(job.id), target_label=job.subject_label, before=before, after=after)  # fmt: skip


async def _finish(
    session: AsyncSession, user: CurrentUser, job: Job, wf: WorkflowDefinition, run: JobRun | None, pair: Pair
) -> dict[str, Any]:
    out = await _run_and_payload(session, user, job, wf, pair, run, freeze=True, pinned=run is not None)
    await session.commit()
    return out


class MarkIn(BaseModel):
    guide_id: uuid.UUID
    version: int
    status: str = Field(pattern=r"^(done|na)$")
    reason: str = Field("", max_length=500)


class ChoiceIn(BaseModel):
    guide_id: uuid.UUID
    version: int
    index: int = Field(ge=0, le=19)


def _node(tree: Tree, node_id: str) -> Any:
    if node_id not in tree.nodes:
        raise NotFound("Không tìm thấy bước trong Playbook.")
    return tree.nodes[node_id]


@router.put("/jobs/{job_id}/marks/{node_id}")
async def put_mark(
    job_id: uuid.UUID, node_id: str, body: MarkIn, user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:  # fmt: skip
    reason = body.reason.strip()
    if body.status == "na" and not 3 <= len(reason) <= 500:
        raise AppError("Cần nhập lý do (từ 3 ký tự) khi đánh dấu “Không áp dụng”.", code="reason_required")
    job, wf, run, tree, pair = await _begin(session, user, job_id, body.guide_id, body.version)
    assert run is not None
    node = _node(tree, node_id)
    if node.kind != "step" or len(node.children) > 1:
        raise AppError("Chỉ đánh dấu được bước làm tay.", code="not_manual_step")
    mark = await session.get(JobMark, (job.id, node_id))
    before = {"node": node_id, "status": mark.status if mark else None}
    if mark is None:
        mark = JobMark(job_id=job.id, node_id=node_id)
        session.add(mark)
    mark.status, mark.reason = body.status, reason if body.status == "na" else None
    mark.done_by, mark.done_at = user.upn, datetime.now(UTC)
    await session.flush()
    await _audit(session, user, job, "playbook.job_mark_set", before,
                 {"node": node_id, "status": body.status, "reason": mark.reason})  # fmt: skip
    return await _finish(session, user, job, wf, run, pair)


@router.delete("/jobs/{job_id}/marks/{node_id}")
async def delete_mark(
    job_id: uuid.UUID, node_id: str, guide_id: uuid.UUID = Query(...), version: int = Query(...),
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:  # fmt: skip
    job, wf, run, _, pair = await _begin(session, user, job_id, guide_id, version, pin=False)
    if mark := await session.get(JobMark, (job.id, node_id)):
        await _audit(session, user, job, "playbook.job_mark_cleared", {"node": node_id, "status": mark.status},
                     {"node": node_id, "status": None})  # fmt: skip
        await session.delete(mark)
        await session.flush()
    return await _finish(session, user, job, wf, run, pair)


@router.put("/jobs/{job_id}/choices/{node_id}")
async def put_choice(
    job_id: uuid.UUID, node_id: str, body: ChoiceIn, user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:  # fmt: skip
    job, wf, run, tree, pair = await _begin(session, user, job_id, body.guide_id, body.version)
    assert run is not None
    node = _node(tree, node_id)
    if not _is_choice(node):
        raise AppError("Bước này không phải bước chọn nhánh.", code="not_choice_step")
    if body.index >= len(node.children):
        raise AppError("Nhánh không tồn tại.", code="invalid_choice")
    before = run.choices.get(node_id)
    if before != body.index:  # the same pick (e.g. asked again inside a loop) changes nothing
        run.choices = run.choices | {node_id: body.index}
        await _audit(session, user, job, "playbook.job_choice_set", {"node": node_id, "choice": before},
                     {"node": node_id, "choice": body.index})  # fmt: skip
    return await _finish(session, user, job, wf, run, pair)


@router.delete("/jobs/{job_id}/choices/{node_id}")
async def delete_choice(
    job_id: uuid.UUID, node_id: str, guide_id: uuid.UUID = Query(...), version: int = Query(...),
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:  # fmt: skip
    job, wf, run, _, pair = await _begin(session, user, job_id, guide_id, version, pin=False)
    if run and node_id in run.choices:
        await _audit(session, user, job, "playbook.job_choice_cleared",
                     {"node": node_id, "choice": run.choices[node_id]}, {"node": node_id, "choice": None})  # fmt: skip
        run.choices = {k: c for k, c in run.choices.items() if k != node_id}
    return await _finish(session, user, job, wf, run, pair)


@router.delete("/jobs/{job_id}/decisions/{node_id}")
async def delete_decision(
    job_id: uuid.UUID, node_id: str, guide_id: uuid.UUID = Query(...), version: int = Query(...),
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:  # fmt: skip
    """ "Kiểm tra lại": drop a frozen check/switch decision; the next run decides it again from the live facts."""
    job, wf, run, _, pair = await _begin(session, user, job_id, guide_id, version, pin=False)
    if run and (old := run.decisions.get(node_id)):
        await _audit(session, user, job, "playbook.job_decision_dropped",
                     {"node": node_id, "branch": old.get("branch")}, {"node": node_id, "branch": None})  # fmt: skip
        run.decisions = {k: d for k, d in run.decisions.items() if k != node_id}
    return await _finish(session, user, job, wf, run, pair)
