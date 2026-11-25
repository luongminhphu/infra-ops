"""Lifecycle-specific document helpers: form metadata, prefill from RTR/SDP data, attach to SDP."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.http import ConnectorHttpError
from app.core.connectors.sdp import SdpClient
from app.core.db import get_session
from app.core.documents.api import document_out
from app.core.documents.service import add_event, get_document
from app.core.errors import AppError, Conflict, NotFound
from app.core.settings.service import load_settings
from app.core.storage.service import read_file
from app.modules.lifecycle.documents import (
    CONDITION_LABEL,
    HANDOVER_CHECKLIST,
    HANDOVER_SOFTWARE,
    KIND_BY_SHORT,
    RETURN_CHECKLIST,
)
from app.modules.lifecycle.models import LifecycleRequest, ReconRow, Snapshot
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import MODULE_ID, SETTINGS_KEY, LifecycleSettings

router = APIRouter(tags=["lifecycle-documents"])
view = require("lifecycle.documents.view")
create = require("lifecycle.documents.create")
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@router.get("/documents/meta")
async def meta(_: CurrentUser = Depends(view)) -> dict[str, Any]:
    return {
        "kinds": {"handover": "lifecycle.handover", "return": "lifecycle.return"},
        "handover_checklist": HANDOVER_CHECKLIST,
        "handover_software": HANDOVER_SOFTWARE,
        "return_checklist": RETURN_CHECKLIST,
        "conditions": CONDITION_LABEL,
    }


@router.get("/documents/prefill")
async def prefill(
    kind: Literal["handover", "return"],
    employee_id: str = Query(min_length=1, max_length=32),
    user: CurrentUser = Depends(create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Suggest form data from the latest RTR request and the latest SDP asset snapshot."""
    settings = await load_settings(session, MODULE_ID, SETTINGS_KEY, LifecycleSettings)
    wanted = {"handover": ("onboard", "transfer"), "return": ("offboard", "transfer")}[kind]
    reqs = (
        await session.scalars(
            select(LifecycleRequest)
            .where(LifecycleRequest.employee_id == employee_id)
            .order_by(LifecycleRequest.created_at.desc())
        )
    ).all()
    req = next((r for r in reqs if r.kind in wanted), reqs[0] if reqs else None)
    fields = (req.fields if req else {}) or {}

    run = await latest_run(session)
    row = None
    assets: list[dict[str, Any]] = []
    if run is not None:
        row = await session.scalar(
            select(ReconRow).where(ReconRow.run_id == run.id, ReconRow.employee_id == employee_id)
        )
        if row is not None and row.data.get("asset_ids"):
            snaps = (
                await session.scalars(
                    select(Snapshot).where(
                        Snapshot.run_id == run.id,
                        Snapshot.system == "sdp_asset",
                        Snapshot.external_id.in_(row.data["asset_ids"]),
                    )
                )
            ).all()
            assets = [s.data for s in snaps]
    if req is None and row is None:
        raise NotFound(f"Không tìm thấy dữ liệu của Mã NV {employee_id}. Đồng bộ dữ liệu hoặc nhập tay.")

    today = datetime.now(TZ).date().isoformat()
    in_use = [a for a in assets if a.get("state") == settings.asset_state_in_use] or assets
    lines: list[dict[str, Any]] = [
        {
            "name": a.get("name") or a.get("product") or "",
            "tag": a.get("asset_tag") or "",
            "date": today,
            "note": "",
            "sdp_asset_id": a.get("id"),
        }
        for a in in_use
    ]
    if kind == "return":
        for line in lines:
            line["condition"] = "good"
    department = fields.get("department") or (row.department if row else "") or ""
    unit = fields.get("unit")
    return {
        "employee": {
            "id": employee_id,
            "full_name": (req.full_name if req else None) or (row.full_name if row else ""),
            "department": f"{department} – {unit}" if unit and department else department,
            "job_title": fields.get("job_title") or "",
            "work_location": fields.get("work_location") or "",
        },
        "date": today,
        "it_rep": user.display_name,
        "reviewer": settings.reviewer_name,
        "request_id": req.id if req else None,
        "request_display_id": req.display_id if req else None,
        "assets": lines,
        "source": {"request": bool(req), "assets_from_sync": run.finished_at if run else None},
    }


class AttachIn(BaseModel):
    request_id: str | None = Field(None, max_length=32, pattern=r"^\d+$")


@router.post("/documents/{doc_id}/attach-sdp")
async def attach_to_sdp(
    doc_id: uuid.UUID,
    body: AttachIn,
    user: CurrentUser = Depends(create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc = await get_document(session, doc_id, for_update=True)
    if doc.kind not in {k.key for k in KIND_BY_SHORT.values()}:
        raise NotFound("Không tìm thấy biên bản.")
    if doc.status != "signed" or doc.signed_file_id is None:
        raise Conflict("Chỉ đính kèm bản đã ký. Tải bản scan đã ký lên trước.", code="not_signed")
    request_id = body.request_id or doc.external_ref
    if not request_id:
        raise AppError(
            "Biên bản chưa gắn với request SDP nào. Nhập số request để đính kèm.", code="no_request"
        )
    settings = await load_settings(session, MODULE_ID, SETTINGS_KEY, LifecycleSettings)
    f, data = await read_file(session, doc.signed_file_id)
    _, ctx = await open_connection(
        session, "sdp", Purpose.WRITE, profile_id=settings.sdp_profile_id, as_user=user.id
    )
    path = settings.sdp_attach_path.replace("{request_id}", request_id)
    try:
        async with SdpClient(ctx) as client:
            await client.post_file(path, settings.sdp_attach_field, f.filename, data, f.mime)
    except ConnectorHttpError as exc:
        add_event(
            doc,
            {
                "type": "sdp_attach",
                "ok": False,
                "request_id": request_id,
                "error": exc.message,
                "by": user.upn,
            },
        )
        await record(
            session,
            actor=user.actor,
            module_id=MODULE_ID,
            system_type="sdp",
            action="documents.sdp_attach",
            result="failure",
            target_type="document",
            target_id=str(doc.id),
            target_label=doc.number,
            message=exc.message,
        )
        await session.commit()
        raise AppError(
            f"SDP từ chối đính kèm: {exc.message}",
            code="sdp_attach_failed",
            details={
                "hint": exc.hint or "Kiểm tra credential Write và đường dẫn upload trong Cấu hình module."
            },
        ) from exc
    add_event(doc, {"type": "sdp_attach", "ok": True, "request_id": request_id, "by": user.upn})
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        system_type="sdp",
        action="documents.sdp_attach",
        target_type="document",
        target_id=str(doc.id),
        target_label=doc.number,
        after={"request_id": request_id},
    )
    await session.commit()
    return document_out(doc)
