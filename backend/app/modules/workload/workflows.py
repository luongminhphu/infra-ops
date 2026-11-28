"""Reassign SDP requests to another technician (runs in the worker, writes back to SDP)."""

from __future__ import annotations

import html
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.models import User
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.sdp import SdpClient
from app.core.errors import AppError, NotFound
from app.core.modules.registry import get_registry
from app.core.rbac.service import permissions_for_user
from app.core.scheduler.service import request_run
from app.core.workflow.definitions import StepContext, StepDefinition, StepOutcome, WorkflowDefinition
from app.core.workflow.models import Job
from app.modules.workload.models import PurchaseRequest, Technician, Ticket
from app.modules.workload.rules import ticket_phase, ticket_state
from app.modules.workload.settings import MODULE_ID
from app.modules.workload.settings import load as load_module_settings

ASSIGN = "workload.tickets.assign"
MANAGE_PURCHASE = "workload.purchase.manage"


async def _preview(ctx: StepContext) -> list[str]:
    p = ctx.params
    return [f"Đổi Technician của {len(p['ticket_ids'])} request trên SDP thành {p['to_technician_name']}."]


async def _reassign(ctx: StepContext) -> StepOutcome:
    """Idempotent: reads each request first and only writes when the technician differs."""
    p = ctx.params
    target = str(p["to_technician_id"])
    settings = await load_module_settings(ctx.session)
    # As the person who started it (reassign: the manager; receive: whoever received the goods).
    _, conn = await open_connection(
        ctx.session, "sdp", Purpose.WRITE, profile_id=settings.sdp_profile_id, as_user=ctx.job.created_by
    )
    before: dict[str, Any] = dict((ctx.step.before_state or {}).get("technicians") or {})
    moved: list[str] = []
    failed: dict[str, str] = {}
    async with SdpClient(conn) as client:
        for rid in p["ticket_ids"]:
            try:
                current = (await client.get_request(rid)).get("technician") or {}
                if str(current.get("id") or "") != target:
                    before.setdefault(rid, current.get("name"))
                    await client.set_request_technician(rid, target)
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
    # Reassignments are visible to coordinators only (labels name other operators).
    view_permission=ASSIGN,
    execute_permission=ASSIGN,
    override_permission=ASSIGN,
)


# ---------------------------------------------------------------- receive purchased goods into SDP stock
async def _receive_preview(ctx: StepContext) -> list[str]:
    p = ctx.params
    return [
        f"Tạo {len(p['units'])} tài sản “{p['product']}” ở trạng thái “{p['state']}” trên SDP "
        f"(serial: {', '.join(u['serial'] for u in p['units'])}); serial đã có thì giữ nguyên."
    ]


async def _receive(ctx: StepContext) -> StepOutcome:
    """Idempotent: looks each serial up first and only creates assets SDP does not have yet."""
    p = ctx.params
    purchase = await ctx.session.get(PurchaseRequest, uuid.UUID(p["purchase_id"]), with_for_update=True)
    if purchase is None:
        return StepOutcome("failed", "Đề xuất không còn tồn tại.", {}, None)
    settings = await load_module_settings(ctx.session)
    # As the person who started it (reassign: the manager; receive: whoever received the goods).
    _, conn = await open_connection(
        ctx.session, "sdp", Purpose.WRITE, profile_id=settings.sdp_profile_id, as_user=ctx.job.created_by
    )
    existing: dict[str, str] = dict((ctx.step.before_state or {}).get("existing") or {})
    assets: list[dict[str, Any]] = []
    failed: dict[str, str] = {}
    async with SdpClient(conn) as client:
        for u in p["units"]:
            try:
                # The app's serial is SDP's Org Serial Number; assets created before that rule only carry it
                # in the serial number field.
                found = await client.find_assets(
                    "org_serial_number", u["serial"]
                ) or await client.find_assets("serial_number", u["serial"])
                if found:
                    existing.setdefault(u["serial"], str(found[0].get("id")))
                    asset = found[0]
                else:
                    asset = await client.create_asset(
                        {
                            "name": u.get("asset_tag") or u["serial"],
                            "product": {"name": p["product"]},
                            "asset_tag": u.get("asset_tag") or None,
                            "org_serial_number": u["serial"],
                            "serial_number": u["serial"],
                            "state": {"name": p["state"]},
                        }
                    )
                assets.append(
                    {"id": str(asset.get("id")), "serial": u["serial"], "asset_tag": u.get("asset_tag")}
                )
            except Exception as exc:
                failed[u["serial"]] = getattr(exc, "message", None) or str(exc)
    output = {"assets": assets, "failed": failed}
    state = {"existing": existing}
    if failed:
        detail = "; ".join(f"{k}: {v}" for k, v in failed.items())
        return StepOutcome("failed", f"Không tạo được {len(failed)} tài sản – {detail}"[:1000], output, state)
    before = purchase.status
    purchase.status, purchase.received_assets, purchase.updated_at = "received", assets, datetime.now(UTC)
    await record(
        ctx.session,
        actor=ctx.actor,
        module_id=MODULE_ID,
        action="workload.purchase_status",
        system_type="sdp",
        target_type="purchase",
        target_id=str(purchase.id),
        target_label=purchase.item,
        before={"status": before},
        after={"status": "received", "assets": [a["id"] for a in assets]},
    )
    try:  # refresh Kho thiết bị so the new stock is suggested for pending onboards
        await request_run(ctx.session, "lifecycle.sync", ctx.actor.upn)
    except NotFound:
        pass  # lifecycle module not installed
    return StepOutcome(
        "done",
        f"Đã nhập kho {len(assets)} tài sản trên SDP ({len(existing)} serial đã có sẵn).",
        output,
        state,
    )


RECEIVE = WorkflowDefinition(
    key="workload.receive",
    title="Nhận hàng – nhập kho SDP",
    steps=[
        StepDefinition(
            key="receive",
            title="Tạo tài sản In Store trên ServiceDesk Plus",
            kind="auto",
            run=_receive,
            preview=_receive_preview,
            skippable=False,
            writes=("sdp",),
        )
    ],
    # Operators receive the goods: they follow and retry their receive jobs; only managers override.
    view_permission="workload.purchase.receive",
    execute_permission="workload.purchase.receive",
    override_permission=MANAGE_PURCHASE,
)


# ---------------------------------------------------------------- quick actions: note / close (as the person)
UPDATE = "workload.tickets.update"


def _suffix(ctx: StepContext) -> str:
    return f" (infra-ops #{str(ctx.job.id)[:8]})"


async def open_job_for_ticket(
    session: AsyncSession, ticket_id: str, exclude: uuid.UUID | None = None
) -> Job | None:
    """Any Job not completed/cancelled (failed included) that works on this request."""
    stmt = select(Job).where(
        Job.status.notin_(("completed", "cancelled")),
        or_(Job.external_ref == ticket_id, Job.params["request_id"].astext == ticket_id),
    )
    if exclude is not None:
        stmt = stmt.where(Job.id != exclude)
    return (await session.scalars(stmt)).first()


def open_job_error(other: Job) -> AppError:
    return AppError(
        f"Request đang có Job chưa xong ({other.subject_label}); hoàn tất Job đó trước khi đóng.",
        code="ticket_has_open_job",
        details={"job_id": str(other.id)},
    )


async def _note_preview(ctx: StepContext) -> list[str]:
    return [f"Thêm ghi chú nội bộ vào request #{ctx.params['ticket_id']} trên SDP."]


async def _note(ctx: StepContext) -> StepOutcome:
    """Idempotent: the job's suffix marks the note, so a retry that finds it does not post twice."""
    p = ctx.params
    settings = await load_module_settings(ctx.session)
    # Always as the person who asked; a missing personal key makes the step wait (never the shared key).
    _, conn = await open_connection(
        ctx.session, "sdp", Purpose.WRITE, profile_id=settings.sdp_profile_id, as_user=ctx.job.created_by
    )
    suffix = _suffix(ctx)
    async with SdpClient(conn) as client:
        notes = await client.request_notes(p["ticket_id"])
        state = {"notes": len(notes)}
        if any(suffix.strip() in str(n.get("description") or "") for n in notes):
            return StepOutcome("done", "Ghi chú đã có trên SDP, không gửi lại.", {"skipped": True}, state)
        text = html.escape(str(p["text"])).replace("\r\n", "\n").replace("\n", "<br>")
        await client.add_note(p["ticket_id"], text + html.escape(suffix))
    return StepOutcome("done", f"Đã thêm ghi chú vào request #{p['ticket_id']}.", {"posted": True}, state)


async def _close_preview(ctx: StepContext) -> list[str]:
    p = ctx.params
    return [f"Đổi trạng thái request #{p['ticket_id']} trên SDP thành “{p['close_status']}”."]


async def _close(ctx: StepContext) -> StepOutcome:
    """Idempotent: reads the request first; only an open request is changed. Never rolled back."""
    p = ctx.params
    target = str(p["close_status"])
    settings = await load_module_settings(ctx.session)
    other = await open_job_for_ticket(ctx.session, p["ticket_id"], exclude=ctx.job.id)
    if other is not None:
        err = open_job_error(other)
        return StepOutcome("failed", err.message, {"job_id": str(other.id)})
    _, conn = await open_connection(
        ctx.session, "sdp", Purpose.WRITE, profile_id=settings.sdp_profile_id, as_user=ctx.job.created_by
    )
    async with SdpClient(conn) as client:
        req = await client.get_request(p["ticket_id"])
        current = (req.get("status") or {}).get("name")
        state = {"status": (ctx.step.before_state or {}).get("status", current)}
        if ticket_state(str(current or ""), settings) != "open":
            note = f"Request đã ở trạng thái “{current}” trên SDP – không đổi."
            target = str(current)
        else:
            if not await _may_close(ctx, (req.get("technician") or {}).get("id")):
                return StepOutcome("failed", "Request đã được chuyển cho người khác.", {}, state)
            await client.set_request_status(p["ticket_id"], target)
            note = f"Request #{p['ticket_id']} đã ở trạng thái “{target}”."
    ticket = await ctx.session.get(Ticket, p["ticket_id"])
    if ticket is not None:
        ticket.status = target
        ticket.state = ticket_state(target, settings)
        ticket.phase = ticket_phase(target, ticket.state, settings)
        ticket.completed_at = ticket.completed_at or datetime.now(UTC) if ticket.state == "done" else None
    return StepOutcome("done", note, {}, state)


async def _may_close(ctx: StepContext, sdp_technician_id: object) -> bool:
    """The creator still owns the request on SDP, or may act on any request (`tickets.assign`)."""
    user = await ctx.session.scalar(select(User).where(User.upn == ctx.job.created_by))
    if user is None:
        return False
    if ASSIGN in await permissions_for_user(ctx.session, user.id, get_registry()):
        return True
    me = await ctx.session.scalar(select(Technician).where(Technician.user_id == user.id))
    return me is not None and sdp_technician_id is not None and str(sdp_technician_id) == me.id


def _note_step(title: str) -> StepDefinition:
    return StepDefinition(
        key="note",
        title=title,
        kind="auto",
        run=_note,
        preview=_note_preview,
        skippable=False,
        writes=("sdp",),
    )


TICKET_NOTE = WorkflowDefinition(
    key="workload.ticket_note",
    title="Ghi chú request",
    steps=[_note_step("Thêm ghi chú vào ServiceDesk Plus")],
    view_permission=UPDATE,
    execute_permission=UPDATE,
    override_permission=ASSIGN,
    view_all_permission=ASSIGN,
)

TICKET_CLOSE = WorkflowDefinition(
    key="workload.ticket_close",
    title="Đóng request",
    steps=[
        _note_step("Ghi chú kết quả xử lý"),
        StepDefinition(
            key="status",
            title="Đổi trạng thái request trên ServiceDesk Plus",
            kind="auto",
            run=_close,
            preview=_close_preview,
            confirm=True,
            confirm_text="Nhập số request (hiển thị ở trên) để xác nhận đóng.",
            skippable=False,
            writes=("sdp",),
        ),
    ],
    required_for_close=("note", "status"),
    view_permission=UPDATE,
    execute_permission=UPDATE,
    override_permission=ASSIGN,
    view_all_permission=ASSIGN,
)
