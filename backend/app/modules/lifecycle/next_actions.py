"""Onboard/offboard items for the "Việc tiếp theo" queue of the job's assignee.

An item with ``passive: True`` needs nothing from the person (e.g. an automatic step that did not run): the
queue shows it apart, never as the next thing to do. Supervisors get the actionable side ("kiểm tra worker").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from app.core.connections.service import personal_key_item
from app.core.workflow.api import stalled_minutes
from app.core.workflow.engine import today_vn
from app.core.workflow.models import Job
from app.modules.lifecycle.assignment import JOB_WORKFLOWS
from app.modules.lifecycle.settings import load as load_module_settings

KIND = {
    "lifecycle.onboard": "Onboard",
    "lifecycle.offboard": "Offboard",
    "lifecycle.transfer": "Thuyên chuyển",
}


def job_item(job: Job, today: Any) -> dict[str, Any] | None:
    """What this job needs from its assignee now, or None when it needs nothing (running, scheduled)."""
    name = f"{KIND.get(job.workflow_key, 'Job')} {job.subject_label}"
    eff = job.params.get("effective_date")
    href = f"/lifecycle/jobs/{job.id}?view=wizard"
    base = {"key": f"job:{job.id}", "kind": "job", "href": href, "due": eff}
    step = next((s for s in job.steps if s.key == job.current_step), None)
    label = f"bước {step.seq}: {step.title}" if step else ""
    if job.status == "failed" and step:
        return {
            **base,
            "priority": 0,
            "title": f"{name} – {label} bị lỗi",
            "detail": step.message or "",
            "action": "Xử lý",
        }
    if job.status == "waiting" and step:
        if step.status == "verify":
            what, action = f"Kiểm tra và xác nhận {label}", "Kiểm tra"
        elif step.requires_confirm and not step.confirmed_at:
            what, action = f"Nhập lại Mã NV để chạy {label}", "Xác nhận"
        elif step.kind == "manual":
            what, action = f"Thực hiện {label}", "Thực hiện"
        else:
            return None  # waiting on a timer / external system
        return {**base, "priority": 0, "title": f"{name} – {what}", "detail": "", "action": action}
    if job.status == "running":
        idle = stalled_minutes(job)
        if idle:
            return {**base, "priority": 3, "passive": True, "title": f"{name} đang chờ hệ thống ở {label}",
                    "action": "Xem", "detail": "Bước tự động chưa chạy. Bạn không cần làm gì – Supervisor được báo để xử lý."}  # fmt: skip
        return None
    if job.status == "draft":
        soon = bool(eff) and str(eff) <= str(today + timedelta(days=3))
        return {**base, "priority": 1 if soon else 2, "title": f"Chuẩn bị {name}", "action": "Chuẩn bị",
                "detail": "Xác nhận thông tin, user SDP và thiết bị rồi bắt đầu."}  # fmt: skip
    return None


async def load(session: AsyncSession, user: Any) -> list[dict[str, Any]]:
    jobs = (
        await session.scalars(
            select(Job).where(
                Job.assignee_user_id == user.id,
                Job.workflow_key.in_(JOB_WORKFLOWS),
                Job.status.not_in(["completed", "cancelled"]),
            )
        )
    ).all()
    today = today_vn()
    items = [i for j in jobs if (i := job_item(j, today))]
    if user.has("lifecycle.job.execute"):  # Job steps read/write SDP with the person's own key
        s = await load_module_settings(session)
        if key := await personal_key_item(session, user.id, "sdp", s.sdp_profile_id):
            items.append(key)
    # Whoever coordinates jobs gets the stalled ones (anyone's) as work: the worker needs checking.
    if user.has("lifecycle.job.assign"):
        running = (
            await session.scalars(
                select(Job)
                .where(Job.workflow_key.in_(JOB_WORKFLOWS), Job.status == "running")
                .options(
                    raiseload(Job.steps), raiseload(Job.evidence), raiseload(Job.assignments)
                )  # only status / updated_at are read
            )
        ).all()
        stalled = [j for j in running if stalled_minutes(j)]
        if stalled:
            items.append(
                {
                    "key": "jobs:stalled",
                    "kind": "job",
                    "priority": 1,
                    "title": f"{len(stalled)} Job đứng yên – kiểm tra worker",
                    "detail": "Bước tự động không chạy: xem log container worker (docker compose logs worker).",
                    "due": None,
                    "href": "/lifecycle/jobs?status=needs",
                    "action": "Kiểm tra",
                }
            )
    return items


async def summary(session: AsyncSession, user: Any) -> list[dict[str, Any]]:
    """My onboard/offboard jobs: completed in 7 days, past the effective date, due within 3 days, open."""
    week_ago = datetime.now(UTC) - timedelta(days=7)
    jobs = (
        await session.scalars(
            select(Job).where(
                Job.assignee_user_id == user.id,
                Job.workflow_key.in_(JOB_WORKFLOWS),
                Job.status != "cancelled",
                or_(
                    Job.status != "completed", Job.closed_at >= week_ago
                ),  # older completed jobs are not counted
            )
        )
    ).all()
    today = today_vn()
    soon = str(today + timedelta(days=3))
    active = [j for j in jobs if j.status != "completed"]

    def eff(j: Job) -> str:
        return str(j.params.get("effective_date") or "")

    def mine_to_do(j: Job) -> bool:
        item = job_item(j, today)
        return bool(item) and not item.get("passive")  # type: ignore[union-attr]

    done = sum(1 for j in jobs if j.status == "completed" and j.closed_at and j.closed_at >= week_ago)
    # Late = past the effective date and waiting on this person (same rule as the queue); a job stuck on an
    # automatic step is not the person's lateness.
    late = sum(1 for j in active if eff(j) and eff(j) < str(today) and mine_to_do(j))
    near = sum(1 for j in active if eff(j) and str(today) <= eff(j) <= soon)
    href = "/lifecycle/jobs?assignee=me&status="
    return [
        {"key": "jobs:done", "group": "done", "label": "Job", "count": done, "href": href + "completed"},
        {"key": "jobs:late", "group": "late", "label": "Job", "count": late, "href": href + "late"},
        {"key": "jobs:soon", "group": "soon", "label": "Job", "count": near, "href": href + "soon"},
        {"key": "jobs:open", "group": "open", "label": "Job", "count": len(active), "href": href + "open"},
    ]
