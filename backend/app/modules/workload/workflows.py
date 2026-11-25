"""Reassign SDP requests to another technician (runs in the worker, writes back to SDP)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.audit.service import record
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.sdp import SdpClient
from app.core.errors import NotFound
from app.core.scheduler.service import request_run
from app.core.settings.service import load_settings
from app.core.workflow.definitions import StepContext, StepDefinition, StepOutcome, WorkflowDefinition
from app.modules.workload.models import PurchaseRequest, Ticket
from app.modules.workload.settings import MODULE_ID, SETTINGS_KEY, WorkloadSettings

ASSIGN = "workload.tickets.assign"
MANAGE_PURCHASE = "workload.purchase.manage"


async def _preview(ctx: StepContext) -> list[str]:
    p = ctx.params
    return [f"Đổi Technician của {len(p['ticket_ids'])} request trên SDP thành {p['to_technician_name']}."]


async def _reassign(ctx: StepContext) -> StepOutcome:
    """Idempotent: reads each request first and only writes when the technician differs."""
    p = ctx.params
    target = str(p["to_technician_id"])
    settings = await load_settings(ctx.session, MODULE_ID, SETTINGS_KEY, WorkloadSettings)
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
    settings = await load_settings(ctx.session, MODULE_ID, SETTINGS_KEY, WorkloadSettings)
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
                found = await client.find_assets("serial_number", u["serial"])
                if found:
                    existing.setdefault(u["serial"], str(found[0].get("id")))
                    asset = found[0]
                else:
                    asset = await client.create_asset(
                        {
                            "name": u.get("asset_tag") or u["serial"],
                            "product": {"name": p["product"]},
                            "asset_tag": u.get("asset_tag") or None,
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
