"""Jobs finished with manual Playbook steps left: a reminder for the assignee (stored decisions only, no fact loads)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from app.core.modules.registry import get_registry
from app.core.workflow.models import Job
from app.modules.playbook.models import GuideVersion, JobMark, JobRun
from app.modules.playbook.run import run_for_job
from app.modules.playbook.tree import Tree

WINDOW = timedelta(days=30)


async def load(session: AsyncSession, user: Any) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Job, JobRun)
            .join(JobRun, JobRun.job_id == Job.id)
            .where(
                Job.assignee_user_id == user.id,
                Job.status == "completed",
                Job.closed_at >= datetime.now(UTC) - WINDOW,
            )
            .options(raiseload(Job.evidence), raiseload(Job.assignments))
        )
    ).all()
    if not rows:
        return []
    ids = [j.id for j, _ in rows]
    marks: dict[Any, dict[str, Any]] = {i: {} for i in ids}
    for m in await session.scalars(select(JobMark).where(JobMark.job_id.in_(ids))):
        marks[m.job_id][m.node_id] = {"status": m.status}
    trees = {
        (v.guide_id, v.version): Tree.model_validate(v.tree)
        for v in await session.scalars(
            select(GuideVersion).where(GuideVersion.guide_id.in_({r.guide_id for _, r in rows}))
        )
    }
    items = []
    for job, run in rows:
        found = get_registry().workflow(job.workflow_key)
        tree = trees.get((run.guide_id, run.version))
        if found is None or tree is None or found[1].view_permission not in user.permissions:
            continue
        r = await run_for_job(
            tree, None, choices=run.choices, decisions=run.decisions, marks=marks[job.id],
            job_steps={s.key: {"status": s.status, "title": s.title} for s in job.steps},
            workflow=job.workflow_key, labels={},
        )  # fmt: skip
        if (
            r["manual_left"] or r["status"] == "needs_choice"
        ):  # not for data gaps a reminder cannot fix (C-15)
            n = r["manual_left"]
            items.append({
                "key": f"playbook:job:{job.id}", "kind": "playbook", "priority": 3,
                "title": f"Còn {n} bước tay trong Playbook" if n else "Playbook chưa đi hết nhánh",
                "detail": job.subject_label, "due": None,
                "href": f"/lifecycle/jobs/{job.id}"  # the only Job page (C-17), "action": "Mở Job",
            })  # fmt: skip
    return items
