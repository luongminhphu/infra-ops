"""Stock management (Tồn kho): new, returned and waiting-for-disposal assets, bulk state changes on SDP.

State changes write SDP from the worker (``lifecycle.asset_state`` / ``lifecycle.asset_dispose`` jobs), with the
requesting person's own API key; disposing is reserved to managers (``lifecycle.stock.dispose``).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.sdp import SdpClient
from app.core.db import get_session
from app.core.errors import AppError, NotFound, PermissionDenied, PersonalCredentialMissing
from app.core.scheduler.service import request_run
from app.core.settings.service import load_settings
from app.core.workflow.definitions import StepContext, StepDefinition, StepOutcome, WorkflowDefinition
from app.core.workflow.engine import create_job, start_job
from app.modules.lifecycle.inventory_api import build_plan
from app.modules.lifecycle.models import AssetReturn, Snapshot
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import MODULE_ID, SETTINGS_KEY, LifecycleSettings

VIEW = "lifecycle.job.view"
MANAGE = "lifecycle.stock.manage"
DISPOSE = "lifecycle.stock.dispose"
MAX_ASSETS = 200

router = APIRouter(prefix="/stock", tags=["lifecycle-stock"])  # mounted under /api/lifecycle


# ------------------------------------------------------------------ worker step
async def _set_state(ctx: StepContext) -> StepOutcome:
    """Idempotent: reads each asset first and only writes when its state differs."""
    p = ctx.params
    s = await load_settings(ctx.session, MODULE_ID, SETTINGS_KEY, LifecycleSettings)
    _, conn = await open_connection(
        ctx.session, "sdp", Purpose.WRITE, profile_id=s.sdp_profile_id, as_user=ctx.job.created_by
    )
    before: dict[str, Any] = dict((ctx.step.before_state or {}).get("states") or {})
    changed: list[str] = []
    failed: dict[str, str] = {}
    async with SdpClient(conn) as client:
        for a in p["assets"]:
            try:
                current = await client.get_asset(a["id"])
                state = ((current.get("state") or {}).get("name")) or ""
                if state != p["to_state"]:
                    before.setdefault(a["id"], state)
                    await client.set_asset_state(a["id"], p["to_state"])
                    changed.append(a["id"])
            except PersonalCredentialMissing:
                raise
            except Exception as exc:
                failed[a.get("tag") or a["id"]] = getattr(exc, "message", None) or str(exc)
    if p["to_state"] == s.asset_state_disposed:  # gone from stock: drop the "returned" marks
        ids = [a["id"] for a in p["assets"]]
        await ctx.session.execute(delete(AssetReturn).where(AssetReturn.asset_id.in_(ids)))
    try:  # refresh the stock list
        await request_run(ctx.session, "lifecycle.sync", ctx.actor.upn)
    except NotFound:
        pass
    out = {"changed": changed, "failed": failed}
    state = {"states": before}
    if failed:
        detail = "; ".join(f"{k}: {v}" for k, v in failed.items())
        return StepOutcome("failed", f"Không đổi được {len(failed)} tài sản – {detail}"[:1000], out, state)
    return StepOutcome(
        "done",
        f"Đã chuyển {len(changed)} tài sản sang “{p['to_state']}” trên SDP ({len(p['assets']) - len(changed)} đã đúng sẵn).",
        out,
        state,
    )


async def _preview(ctx: StepContext) -> list[str]:
    p = ctx.params
    return [f"Đổi trạng thái {len(p['assets'])} tài sản trên SDP thành “{p['to_state']}”."]


def _definition(key: str, title: str, permission: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        key=key,
        title=title,
        steps=[
            StepDefinition(
                key="set_state",
                title="Đổi trạng thái tài sản trên ServiceDesk Plus",
                kind="auto",
                run=_set_state,
                preview=_preview,
                skippable=False,
                writes=("sdp",),
            )
        ],
        view_permission=VIEW,
        # Retrying a disposal is disposing: only those allowed to dispose can run or retry it.
        execute_permission=permission,
        override_permission=permission,
    )


ASSET_STATE = _definition("lifecycle.asset_state", "Cập nhật trạng thái tồn kho", MANAGE)
ASSET_DISPOSE = _definition("lifecycle.asset_dispose", "Thanh lý tài sản", DISPOSE)


# ------------------------------------------------------------------ API
STATUS_LABEL = {"new": "Mới", "returned": "Thu hồi", "pending": "Chờ thanh lý"}


@router.get("")
async def stock(
    user: CurrentUser = Depends(require(VIEW)), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Assets in stock from the latest sync: new, returned (thu hồi) and waiting for disposal."""
    s = await load_settings(session, MODULE_ID, SETTINGS_KEY, LifecycleSettings)
    run = await latest_run(session)
    if run is None:
        return {"as_of": None, "items": [], "states": _states(s), "can": _can(user)}
    rows = (
        await session.scalars(
            select(Snapshot).where(Snapshot.run_id == run.id, Snapshot.system == "sdp_asset")
        )
    ).all()
    marks = {m.asset_id: m for m in (await session.scalars(select(AssetReturn))).all()}
    plan = await build_plan(session)
    reserved = {
        i["id"]: i["reserved_for"] for line in plan["stock"] for i in line["items"] if i.get("reserved_for")
    }
    items = []
    for r in rows:
        a = r.data
        if a.get("user_id"):
            continue
        if a.get("state") == s.asset_state_pending_disposal:
            status = "pending"
        elif a.get("state") == s.asset_state_in_store:
            status = "returned" if a["id"] in marks else "new"
        else:
            continue
        m = marks.get(a["id"])
        items.append(
            {
                "id": a["id"],
                "name": a.get("name"),
                "asset_tag": a.get("asset_tag"),
                "serial": a.get("serial"),
                "product": a.get("product"),
                "product_type": a.get("product_type"),
                "status": status,
                "returned_at": m.returned_at if m else None,
                "returned_from": m.from_user if m else None,
                "reserved_for": reserved.get(a["id"]),
                # Asset tag and serial are required to manage stock: flagged so they get fixed on SDP.
                "missing": [
                    k for k, v in (("asset_tag", a.get("asset_tag")), ("serial", a.get("serial"))) if not v
                ],
            }
        )
    items.sort(key=lambda i: ((i["product_type"] or ""), (i["asset_tag"] or i["name"] or "")))
    return {"as_of": run.finished_at, "items": items, "states": _states(s), "can": _can(user)}


def _states(s: LifecycleSettings) -> dict[str, str]:
    return {
        "in_store": s.asset_state_in_store,
        "pending": s.asset_state_pending_disposal,
        "disposed": s.asset_state_disposed,
    }


def _can(user: CurrentUser) -> dict[str, bool]:
    return {"manage": user.has(MANAGE), "dispose": user.has(DISPOSE)}


class MarkIn(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=MAX_ASSETS)
    returned: bool


@router.post("/returned")
async def mark_returned(
    body: MarkIn, user: CurrentUser = Depends(require(MANAGE)), session: AsyncSession = Depends(get_session)
) -> dict[str, int]:
    """Mark stock as returned (thu hồi) or back to new, in infra-ops only (SDP has no such state)."""
    ids = list(dict.fromkeys(body.asset_ids))
    if body.returned:
        await session.execute(
            insert(AssetReturn)
            .values([{"asset_id": i, "source": "manual", "marked_by": user.upn} for i in ids])
            .on_conflict_do_nothing()
        )
    else:
        await session.execute(delete(AssetReturn).where(AssetReturn.asset_id.in_(ids)))
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stock_marked",
        target_type="asset",
        target_id=ids[0],
        target_label=f"{len(ids)} tài sản",
        after={"returned": body.returned, "asset_ids": ids},
    )
    await session.commit()
    return {"updated": len(ids)}


class StateIn(BaseModel):
    assets: list[dict[str, str]] = Field(min_length=1, max_length=MAX_ASSETS)  # {id, tag, name}
    to: Literal["pending", "in_store", "disposed"]


@router.post("/state", status_code=201)
async def change_state(
    body: StateIn, user: CurrentUser = Depends(require(VIEW)), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Start a worker job that sets the SDP state of the selected assets (with the person's own key)."""
    if not user.has(DISPOSE if body.to == "disposed" else MANAGE):
        raise PermissionDenied(
            "Chỉ Manager hoặc Administrator được thanh lý tài sản."
            if body.to == "disposed"
            else "Bạn không có quyền cập nhật tồn kho."
        )
    s = await load_settings(session, MODULE_ID, SETTINGS_KEY, LifecycleSettings)
    assets = [
        {"id": str(a.get("id", "")), "tag": a.get("tag") or "", "name": a.get("name") or ""}
        for a in body.assets
    ]
    if any(not a["id"] for a in assets):
        raise AppError("Thiếu mã tài sản SDP.", code="invalid_assets")
    if body.to == "disposed":
        run = await latest_run(session)
        waiting = (
            {
                r.external_id
                for r in (
                    await session.scalars(
                        select(Snapshot).where(Snapshot.run_id == run.id, Snapshot.system == "sdp_asset")
                    )
                ).all()
                if r.data.get("state") == s.asset_state_pending_disposal
            }
            if run
            else set()
        )
        if not_waiting := [a["tag"] or a["id"] for a in assets if a["id"] not in waiting]:
            raise AppError(
                f"Chỉ thanh lý tài sản đang “{s.asset_state_pending_disposal}”: {', '.join(not_waiting[:5])}.",
                code="not_pending_disposal",
            )
    to_state = {
        "pending": s.asset_state_pending_disposal,
        "in_store": s.asset_state_in_store,
        "disposed": s.asset_state_disposed,
    }[body.to]
    wf = "lifecycle.asset_dispose" if body.to == "disposed" else "lifecycle.asset_state"
    job, _ = await create_job(
        session,
        user.actor,
        wf,
        params={"assets": assets, "to_state": to_state},
        subject_key=f"{len(assets)} tài sản",
        subject_label=f"{len(assets)} tài sản → {to_state}",
        external_ref=None,
        idempotency_key=f"{wf}:{uuid.uuid4()}",
    )
    await start_job(session, user.actor, job, None)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stock_state_requested",
        system_type="sdp",
        target_type="job",
        target_id=str(job.id),
        target_label=f"{len(assets)} tài sản → {to_state}",
        after={"to": to_state, "asset_ids": [a["id"] for a in assets]},
    )
    await session.commit()
    return {"job_id": str(job.id)}
