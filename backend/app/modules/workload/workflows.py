"""Reassign SDP requests to another technician (runs in the worker, writes back to SDP)."""

from __future__ import annotations

from typing import Any

from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.sdp import SdpClient
from app.core.settings.service import load_settings
from app.core.workflow.definitions import StepContext, StepDefinition, StepOutcome, WorkflowDefinition
from app.modules.workload.models import Ticket
from app.modules.workload.settings import MODULE_ID, SETTINGS_KEY, WorkloadSettings

VIEW = "workload.tickets.view"
ASSIGN = "workload.tickets.assign"


async def _preview(ctx: StepContext) -> list[str]:
    p = ctx.params
    return [f"Đổi Technician của {len(p['ticket_ids'])} request trên SDP thành {p['to_technician_name']}."]


async def _reassign(ctx: StepContext) -> StepOutcome:
    """Idempotent: reads each request first and only writes when the technician differs."""
    p = ctx.params
    target = str(p["to_technician_id"])
    settings = await load_settings(ctx.session, MODULE_ID, SETTINGS_KEY, WorkloadSettings)
    _, conn = await open_connection(ctx.session, "sdp", Purpose.WRITE, profile_id=settings.sdp_profile_id)
    before: dict[str, Any] = dict((ctx.step.before_state or {}).get("technicians") or {})
    moved: list[str] = []
    failed: dict[str, str] = {}
    async with SdpClient(conn) as client:
        for rid in p["ticket_ids"]:
            try:
                current = (await client.get_request(rid)).get("technician") or {}
                if str(current.get("id") or "") != target:
                    before.setdefault(rid, current.get("name"))
                    await client.write(
                        "PUT", f"/api/v3/requests/{rid}", {"request": {"technician": {"id": target}}}
                    )
                    moved.append(rid)
                ticket = await ctx.session.get(Ticket, rid)
                if ticket is not None:
                    ticket.technician_id, ticket.technician_name = target, p["to_technician_name"]
            except Exception as exc:
                failed[rid] = getattr(exc, "message", None) or str(exc)
    output = {"moved": moved, "failed": failed}
    state = {"technicians": before}
    if failed:
        detail = "; ".join(f"#{k}: {v}" for k, v in failed.items())
        return StepOutcome(
            "failed", f"Không chuyển được {len(failed)} request – {detail}"[:1000], output, state
        )
    return StepOutcome(
        "done", f"Đã chuyển {len(p['ticket_ids'])} request cho {p['to_technician_name']}.", output, state
    )


REASSIGN = WorkflowDefinition(
    key="workload.reassign",
    title="Chuyển việc",
    steps=[
        StepDefinition(
            key="reassign",
            title="Đổi Technician trên ServiceDesk Plus",
            kind="auto",
            run=_reassign,
            preview=_preview,
            skippable=False,
            writes=("sdp",),
        )
    ],
    view_permission=VIEW,
    execute_permission=ASSIGN,
    override_permission=ASSIGN,
)
