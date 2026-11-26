"""Who is responsible for an onboard/offboard job: the SDP technician of its RTR request.

SDP stays the source of truth. Sync copies the request's technician onto the job; a manager's
reassignment in infra-ops writes the new technician to SDP first (worker step), then updates the job.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from app.core.auth.models import User
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.sdp import SdpClient
from app.core.workflow import engine
from app.core.workflow.definitions import StepContext, StepDefinition, StepOutcome, WorkflowDefinition
from app.core.workflow.models import Job
from app.modules.lifecycle.models import LifecycleRequest
from app.modules.lifecycle.settings import load as load_module_settings

ASSIGN = "lifecycle.job.assign"
JOB_WORKFLOWS = ("lifecycle.onboard", "lifecycle.offboard", "lifecycle.transfer")


async def user_for_email(session: AsyncSession, email: str | None) -> User | None:
    if not email:
        return None
    e = email.lower()
    return await session.scalar(
        select(User).where((func.lower(User.email) == e) | (func.lower(User.upn) == e))
    )


async def assign_from_request(session: AsyncSession, job: Job, req: LifecycleRequest) -> bool:
    if not req.technician_name or job.status in ("completed", "cancelled"):
        return False
    user = await user_for_email(session, req.technician_email)
    return await engine.set_assignee(
        session, job, user_id=user.id if user else None, name=req.technician_name, actor="SDP", source="sdp"
    )


async def sync_assignees(session: AsyncSession) -> int:
    """After a sync: open jobs follow the technician of their request on SDP."""
    rows = (
        await session.execute(
            select(Job, LifecycleRequest)
            .join(LifecycleRequest, LifecycleRequest.id == Job.external_ref)
            .where(Job.workflow_key.in_(JOB_WORKFLOWS), Job.status.notin_(["completed", "cancelled"]))
            .options(
                raiseload(Job.steps), raiseload(Job.evidence)
            )  # set_assignee appends to job.assignments: keep it
        )
    ).all()
    return sum([await assign_from_request(session, job, req) for job, req in rows])


async def _reassign(ctx: StepContext) -> StepOutcome:
    """Read first: find the technician by e-mail, write only when the request has someone else."""
    p = ctx.params
    s = await load_module_settings(ctx.session)
    # Run as whoever asked for the hand-over: SDP records them as the one who changed the technician.
    _, conn = await open_connection(
        ctx.session, "sdp", Purpose.WRITE, profile_id=s.sdp_profile_id, as_user=ctx.job.created_by
    )
    async with SdpClient(conn) as c:
        found = await c.list_all(
            "/api/v3/technicians",
            "technicians",
            search_criteria={"field": "email_id", "condition": "is", "value": p["to_email"]},
            max_rows=5,
        )
        if not found:
            return StepOutcome("failed", f"Không tìm thấy technician trên SDP có e-mail {p['to_email']}.")
        tech = found[0]
        current = (await c.get_request(p["request_id"])).get("technician") or {}
        before = {"technician": current.get("name")}
        if str(current.get("id") or "") != str(tech["id"]):
            await c.set_request_technician(p["request_id"], tech["id"])
    req = await ctx.session.get(LifecycleRequest, p["request_id"])
    if req is not None:
        req.technician_id, req.technician_name, req.technician_email = (
            str(tech["id"]),
            tech.get("name"),
            p["to_email"],
        )
    target = await engine.get_job(ctx.session, uuid.UUID(p["job_id"]), for_update=True)
    await engine.set_assignee(
        ctx.session,
        target,
        user_id=uuid.UUID(p["to_user_id"]),
        name=tech.get("name") or p["to_name"],
        actor=p["requested_by"],
        source="app",
        reason=p.get("reason", ""),
    )
    return StepOutcome(
        "done",
        f"Request #{p['request_display_id']} giao cho {tech.get('name')} trên SDP.",
        before_state=before,
    )


async def _preview(ctx: StepContext) -> list[str]:
    p = ctx.params
    return [f"Đổi Technician của request #{p['request_display_id']} trên SDP thành {p['to_name']}."]


REASSIGN_JOB = WorkflowDefinition(
    key="lifecycle.assign",
    title="Chuyển Job onboard/offboard",
    steps=[
        StepDefinition(
            key="reassign",
            title="Đổi Technician của request trên SDP",
            kind="auto",
            run=_reassign,
            preview=_preview,
            skippable=False,
            writes=("sdp",),
        )
    ],
    view_permission=ASSIGN,  # hand-overs are the managers' business
    execute_permission=ASSIGN,
    override_permission=ASSIGN,
)


def assignment_params(job: Job, req: LifecycleRequest, user: User, by: str, reason: str) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "request_id": req.id,
        "request_display_id": req.display_id or req.id,
        "to_user_id": str(user.id),
        "to_name": user.display_name,
        "to_email": (user.email or user.upn).lower(),
        "requested_by": by,
        "reason": reason,
    }
