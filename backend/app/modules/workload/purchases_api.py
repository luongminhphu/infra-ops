"""Device purchase proposals: suggested from SDP requests about replacing, upgrading or repairing a
device, kept in infra-ops and exported for the purchasing team (nothing is written to SDP)."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AppError, NotFound
from app.core.settings.service import load_settings
from app.core.storage.service import check_size, read_file, safe_filename, save_file, sniff
from app.core.workflow.engine import create_job, start_job
from app.core.workflow.models import Job
from app.modules.workload import po as pos
from app.modules.workload.api import VIEW_ALL, _my_technician, ticket_out
from app.modules.workload.models import (
    CatalogItem,
    PurchaseEvidence,
    PurchaseOrder,
    PurchaseRequest,
    Ticket,
)
from app.modules.workload.settings import MODULE_ID, SETTINGS_KEY, WorkloadSettings

router = APIRouter(tags=["workload"])
view = require("workload.tickets.view")
create = require("workload.purchase.create")
manage = require("workload.purchase.manage")
catalog_manage = require("workload.catalog.manage")
po_close = require("workload.po.close")
EVIDENCE_MIME = {"image/png", "image/jpeg", "application/pdf"}
MAX_EVIDENCE = 10

Kind = Literal["replace", "upgrade", "repair", "new"]
Status = Literal["proposed", "approved", "ordered", "received", "rejected"]
KIND_LABEL = {"replace": "Thay thế", "upgrade": "Nâng cấp", "repair": "Sửa chữa", "new": "Cấp mới"}
STATUS_LABEL = {
    "proposed": "Đã đề xuất",
    "approved": "Đã duyệt",
    "ordered": "Đã đặt mua",
    "received": "Đã nhận hàng",
    "rejected": "Từ chối",
}
# Allowed manual status changes. "ordered" is set only by closing the PO; a rejection can be re-opened.
NEXT: dict[str, set[str]] = {
    "proposed": {"approved", "rejected"},
    "approved": {"rejected"},
    "ordered": set(),  # → received via POST /purchases/{id}/receive (creates the SDP assets)
    "received": set(),
    "rejected": {"proposed"},
}


def out(
    p: PurchaseRequest,
    cat: CatalogItem | None = None,
    evidence: list[PurchaseEvidence] | None = None,
    po: PurchaseOrder | None = None,
    job: Job | None = None,
) -> dict[str, Any]:
    price = pos.price_of(p, cat)
    return {
        "id": str(p.id),
        "ticket_id": p.ticket_id,
        "ticket_display_id": p.ticket_display_id,
        "source": p.source,
        "source_ref": p.source_ref,
        "employee": p.employee,
        "kind": p.kind,
        "item": p.item,
        "catalog_item": {"id": str(cat.id), "name": cat.name} if cat else None,
        "quantity": p.quantity,
        "unit_price": price,
        "amount": None if price is None else price * p.quantity,
        "reason": p.reason,
        "status": p.status,
        "note": p.note,
        "created_by": p.created_by,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "approved_by": p.approved_by,
        "evidence": [
            {
                "id": str(e.id),
                "filename": e.filename,
                "mime": e.mime,
                "created_by": e.created_by,
                "created_at": e.created_at,
            }
            for e in evidence or []
        ],
        "po": {"id": str(po.id), "number": po.number, "status": po.status} if po else None,
        "next": sorted(NEXT[p.status]),
        "sdp_product": cat.sdp_product if cat else "",
        "received_assets": p.received_assets,
        "receive_job": None
        if job is None
        else {
            "id": str(job.id),
            "status": job.status,
            "error": next((st.message for st in job.steps if st.status == "failed"), None),
        },
    }


async def _full(session: AsyncSession, rows: list[PurchaseRequest]) -> list[dict[str, Any]]:
    """Serialize proposals with their catalog item, evidence and PO (three queries, no N+1)."""
    cat_ids = {p.catalog_item_id for p in rows if p.catalog_item_id}
    po_ids = {p.po_id for p in rows if p.po_id}
    cats = {c.id: c for c in (await session.scalars(select(CatalogItem).where(CatalogItem.id.in_(cat_ids)))).all()} if cat_ids else {}  # fmt: skip
    po_rows = {o.id: o for o in (await session.scalars(select(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids)))).all()} if po_ids else {}  # fmt: skip
    ev: dict[uuid.UUID, list[PurchaseEvidence]] = {}
    if rows:
        for e in (
            await session.scalars(
                select(PurchaseEvidence)
                .where(PurchaseEvidence.purchase_id.in_([p.id for p in rows]))
                .order_by(PurchaseEvidence.created_at)
            )
        ).all():
            ev.setdefault(e.purchase_id, []).append(e)
    jobs: dict[str, Job] = {}
    ordered = [str(p.id) for p in rows if p.status == "ordered"]
    if ordered:
        for j in (
            await session.scalars(
                select(Job)
                .where(
                    Job.workflow_key == "workload.receive",
                    Job.external_ref.in_(ordered),
                    Job.status != "cancelled",
                )
                .order_by(Job.created_at)
            )
        ).all():
            jobs[j.external_ref] = j  # type: ignore[index]
    return [
        out(p, cats.get(p.catalog_item_id), ev.get(p.id, []), po_rows.get(p.po_id), jobs.get(str(p.id)))  # type: ignore[arg-type]
        for p in rows
    ]


async def _one(session: AsyncSession, p: PurchaseRequest) -> dict[str, Any]:
    return (await _full(session, [p]))[0]


async def _settings(session: AsyncSession) -> WorkloadSettings:
    return await load_settings(session, MODULE_ID, SETTINGS_KEY, WorkloadSettings)


async def _visible(
    session: AsyncSession, user: CurrentUser, purchase_id: uuid.UUID, *, lock: bool = False
) -> PurchaseRequest:
    p = await session.get(PurchaseRequest, purchase_id, with_for_update=lock)
    if p is None or (not user.has(VIEW_ALL) and p.created_by != user.upn):
        raise NotFound("Không tìm thấy đề xuất.")
    return p


def _scoped(user: CurrentUser) -> Any:
    stmt = select(PurchaseRequest)
    return stmt if user.has(VIEW_ALL) else stmt.where(PurchaseRequest.created_by == user.upn)


@router.get("/purchases")
async def list_purchases(
    user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    status: str = Query("", max_length=16),
    source_ref: str = Query("", max_length=64),
) -> dict[str, Any]:
    stmt = _scoped(user)
    if status:
        stmt = stmt.where(PurchaseRequest.status == status)
    if source_ref:
        stmt = stmt.where(PurchaseRequest.source_ref == source_ref)
    rows = (await session.scalars(stmt.order_by(PurchaseRequest.created_at.desc()).limit(1000))).all()
    scoped = _scoped(user).subquery()
    counts = dict(
        (await session.execute(select(scoped.c.status, func.count()).group_by(scoped.c.status))).all()
    )
    return {"items": await _full(session, list(rows)), "counts": counts}


class PurchaseIn(BaseModel):
    ticket_id: str | None = Field(None, max_length=32)
    source: Literal["ticket", "onboard", "manual"] = "manual"
    source_ref: str | None = Field(None, max_length=64)
    employee: str | None = Field(None, max_length=256)
    catalog_item_id: uuid.UUID | None = None
    kind: Kind
    item: str = Field(min_length=2, max_length=300)
    quantity: int = Field(1, ge=1, le=500)
    reason: str = Field("", max_length=2000)


async def _catalog(session: AsyncSession, item_id: uuid.UUID | None) -> CatalogItem | None:
    if item_id is None:
        return None
    cat = await session.get(CatalogItem, item_id)
    if cat is None or not cat.active:
        raise NotFound("Không tìm thấy thiết bị trong bảng giá.")
    return cat


@router.post("/purchases", status_code=201)
async def create_purchase(
    body: PurchaseIn, user: CurrentUser = Depends(create), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    display = None
    if body.ticket_id:
        ticket = await session.get(Ticket, body.ticket_id)
        if ticket is None:
            raise NotFound("Không tìm thấy request.")
        display = ticket.display_id
    await _catalog(session, body.catalog_item_id)
    p = PurchaseRequest(
        ticket_id=body.ticket_id,
        ticket_display_id=display,
        source="ticket" if body.ticket_id else body.source,
        source_ref=display if body.ticket_id else (body.source_ref or None),
        employee=(body.employee or "").strip() or None,
        catalog_item_id=body.catalog_item_id,
        kind=body.kind,
        item=body.item.strip(),
        quantity=body.quantity,
        reason=body.reason.strip(),
        status="proposed",
        created_by=user.upn,
    )
    session.add(p)
    await session.flush()
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.purchase_created",
        target_type="purchase",
        target_id=str(p.id),
        target_label=p.item,
        after={"kind": p.kind, "quantity": p.quantity, "source": p.source, "ref": p.source_ref},
    )
    await session.commit()
    await session.refresh(p)
    return await _one(session, p)


class StatusIn(BaseModel):
    status: Status
    note: str = Field("", max_length=1000)
    # The supervisor can link (or fix) the price-list item when approving.
    catalog_item_id: uuid.UUID | None = None


@router.patch("/purchases/{purchase_id}")
async def update_purchase(
    purchase_id: uuid.UUID,
    body: StatusIn,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    p = await session.get(PurchaseRequest, purchase_id, with_for_update=True)
    if p is None:
        raise NotFound("Không tìm thấy đề xuất.")
    if body.status not in NEXT[p.status]:
        hint = " PO được đặt mua khi Supervisor chốt PO." if body.status == "ordered" else ""
        raise AppError(
            f"Không chuyển được từ “{STATUS_LABEL[p.status]}” sang “{STATUS_LABEL[body.status]}”.{hint}",
            code="invalid_transition",
        )
    if body.status == "rejected" and len(body.note.strip()) < 3:
        raise AppError("Ghi lý do từ chối.", code="reason_required")
    if body.catalog_item_id is not None and p.status == "proposed":
        p.catalog_item_id = (await _catalog(session, body.catalog_item_id)).id  # type: ignore[union-attr]
    if body.status == "approved":
        n_ev = await session.scalar(select(func.count()).where(PurchaseEvidence.purchase_id == p.id))
        if not n_ev:
            raise AppError(
                "Chưa có bằng chứng Function Head đã duyệt – Operator cần đính kèm trước khi duyệt.",
                code="evidence_required",
            )
        cat = await session.get(CatalogItem, p.catalog_item_id) if p.catalog_item_id else None
        if cat is None or not cat.unit_price:
            raise AppError(
                "Chưa có đơn giá: chọn thiết bị trong bảng giá (Supervisor cập nhật giá sau khi có báo giá).",
                code="price_required",
            )
        p.approved_by, p.approved_at = user.upn, datetime.now(UTC)
    before = p.status
    p.status, p.updated_at = body.status, datetime.now(UTC)
    if body.status == "rejected":
        p.po_id = None
    if body.note.strip():
        p.note = body.note.strip()
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.purchase_status",
        target_type="purchase",
        target_id=str(p.id),
        target_label=p.item,
        before={"status": before},
        after={"status": p.status, "note": body.note.strip() or None},
    )
    if "approved" in (before, body.status):
        await pos.repack(session, await _settings(session))
    await session.commit()
    await session.refresh(p)
    return await _one(session, p)


class UnitIn(BaseModel):
    serial: str = Field(min_length=2, max_length=100)
    asset_tag: str = Field("", max_length=100)


class ReceiveIn(BaseModel):
    units: list[UnitIn] = Field(default_factory=list, max_length=500)
    note: str = Field("", max_length=1000)


@router.post("/purchases/{purchase_id}/receive")
async def receive(
    purchase_id: uuid.UUID,
    body: ReceiveIn,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Goods arrived. Devices tracked on SDP (catalog item has an SDP product) get one asset per unit,
    created In Store by a worker job; anything else (parts, repairs) is simply marked received."""
    p = await session.get(PurchaseRequest, purchase_id, with_for_update=True)
    if p is None:
        raise NotFound("Không tìm thấy đề xuất.")
    if p.status != "ordered":
        raise AppError("Chỉ nhận hàng cho đề xuất đã đặt mua (PO đã chốt).", code="not_ordered")
    running = await session.scalar(
        select(Job.id).where(
            Job.workflow_key == "workload.receive",
            Job.external_ref == str(p.id),
            Job.status.not_in(["completed", "cancelled"]),
        )
    )
    if running:
        raise AppError(
            "Đang nhập kho đề xuất này – xem tiến độ hoặc thử lại bước lỗi.", code="receive_running"
        )
    cat = await session.get(CatalogItem, p.catalog_item_id) if p.catalog_item_id else None
    if cat is None or not cat.sdp_product:
        before = p.status
        p.status, p.updated_at = "received", datetime.now(UTC)
        if body.note.strip():
            p.note = body.note.strip()
        await record(
            session,
            actor=user.actor,
            module_id=MODULE_ID,
            action="workload.purchase_status",
            target_type="purchase",
            target_id=str(p.id),
            target_label=p.item,
            before={"status": before},
            after={"status": "received", "sdp_assets": False},
        )
        await session.commit()
        return await _one(session, p)

    serials = [u.serial.strip() for u in body.units]
    if len(serials) != p.quantity:
        raise AppError(f"Nhập đủ serial cho {p.quantity} thiết bị.", code="units_mismatch")
    if len({x.lower() for x in serials}) != len(serials):
        raise AppError("Serial bị trùng.", code="duplicate_serial")
    settings = await _settings(session)
    job, _ = await create_job(
        session,
        user.actor,
        "workload.receive",
        params={
            "purchase_id": str(p.id),
            "product": cat.sdp_product,
            "state": settings.asset_state_in_store,
            "units": [{"serial": u.serial.strip(), "asset_tag": u.asset_tag.strip()} for u in body.units],
        },
        subject_key=str(p.id),
        subject_label=f"Nhập kho {p.quantity} × {cat.name}",
        external_ref=str(p.id),
        idempotency_key=f"workload.receive:{uuid.uuid4()}",
    )
    await start_job(session, user.actor, job, None)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.receive_requested",
        system_type="sdp",
        target_type="purchase",
        target_id=str(p.id),
        target_label=p.item,
        after={"serials": serials, "product": cat.sdp_product},
    )
    await session.commit()
    return await _one(session, p)


# ---------------------------------------------------------------- Function Head approval evidence
async def _evidence_target(
    session: AsyncSession, user: CurrentUser, purchase_id: uuid.UUID
) -> PurchaseRequest:
    p = await _visible(session, user, purchase_id, lock=True)
    if p.created_by != user.upn and not user.has("workload.purchase.manage"):
        raise NotFound("Không tìm thấy đề xuất.")
    if p.status != "proposed":
        raise AppError("Chỉ sửa bằng chứng khi đề xuất chưa được duyệt.", code="evidence_locked")
    return p


@router.post("/purchases/{purchase_id}/evidence", status_code=201)
async def add_evidence(
    purchase_id: uuid.UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    p = await _evidence_target(session, user, purchase_id)
    count = await session.scalar(select(func.count()).where(PurchaseEvidence.purchase_id == p.id))
    if (count or 0) >= MAX_EVIDENCE:
        raise AppError(f"Tối đa {MAX_EVIDENCE} file bằng chứng mỗi đề xuất.", code="too_much_evidence")
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    check_size(data)
    mime = sniff(data, EVIDENCE_MIME)
    name = safe_filename(file.filename or "phe-duyet")
    stored = await save_file(session, data, name, mime, user.upn)
    ev = PurchaseEvidence(purchase_id=p.id, file_id=stored.id, filename=name, mime=mime, created_by=user.upn)
    session.add(ev)
    p.updated_at = datetime.now(UTC)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.purchase_evidence_added",
        target_type="purchase",
        target_id=str(p.id),
        target_label=p.item,
        after={"filename": name, "mime": mime},
    )
    await session.commit()
    return await _one(session, p)


@router.delete("/purchases/{purchase_id}/evidence/{evidence_id}")
async def delete_evidence(
    purchase_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: CurrentUser = Depends(create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    p = await _evidence_target(session, user, purchase_id)
    ev = await session.get(PurchaseEvidence, evidence_id)
    if ev is None or ev.purchase_id != p.id:
        raise NotFound("Không tìm thấy bằng chứng.")
    await session.delete(ev)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.purchase_evidence_removed",
        target_type="purchase",
        target_id=str(p.id),
        target_label=p.item,
        before={"filename": ev.filename},
    )
    await session.commit()
    return await _one(session, p)


@router.get("/purchases/{purchase_id}/evidence/{evidence_id}")
async def evidence_file(
    purchase_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
) -> Response:
    p = await _visible(session, user, purchase_id)
    ev = await session.get(PurchaseEvidence, evidence_id)
    if ev is None or ev.purchase_id != p.id:
        raise NotFound("Không tìm thấy bằng chứng.")
    stored, data = await read_file(session, ev.file_id)
    return Response(
        data,
        media_type=stored.mime,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(ev.filename)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/tickets/{ticket_id}/dismiss-purchase", status_code=204)
async def dismiss_suggestion(
    ticket_id: str, user: CurrentUser = Depends(create), session: AsyncSession = Depends(get_session)
) -> None:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFound("Không tìm thấy request.")
    ticket.po_dismissed = True
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.purchase_dismissed",
        target_type="ticket",
        target_id=ticket.id,
        target_label=ticket.subject[:200],
    )
    await session.commit()


@router.get("/purchases/export")
async def export_purchases(
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> Response:
    rows = await _full(
        session, list((await session.scalars(_scoped(user).order_by(PurchaseRequest.created_at))).all())
    )
    wb = Workbook()
    ws = wb.active
    ws.title = "De xuat mua thiet bi"
    ws.append(
        [
            "Ngày",
            "Nguồn",
            "Người dùng",
            "Loại",
            "Thiết bị",
            "Số lượng",
            "Đơn giá (đ)",
            "Thành tiền (đ)",
            "Lý do",
            "Trạng thái",
            "PO",
            "Ghi chú",
            "Người đề xuất",
        ]
    )
    for p in rows:
        ws.append(
            [
                p["created_at"].astimezone(pos.TZ).strftime("%d/%m/%Y"),
                f"#{p['ticket_display_id']}" if p["ticket_display_id"] else (p["source_ref"] or ""),
                p["employee"] or "",
                KIND_LABEL.get(p["kind"], p["kind"]),
                p["catalog_item"]["name"] if p["catalog_item"] else p["item"],
                p["quantity"],
                p["unit_price"],
                p["amount"],
                p["reason"],
                STATUS_LABEL.get(p["status"], p["status"]),
                (p["po"]["number"] or "PO nháp") if p["po"] else "",
                p["note"],
                p["created_by"],
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="de-xuat-mua-thiet-bi.xlsx"'},
    )


# ---------------------------------------------------------------- price list (catalog)
def catalog_out(c: CatalogItem) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name,
        "category": c.category,
        "spec": c.spec,
        "unit_price": c.unit_price,
        "sdp_product": c.sdp_product,
        "vendor": c.vendor,
        "quoted_at": c.quoted_at,
        "active": c.active,
        "updated_by": c.updated_by,
        "updated_at": c.updated_at,
    }


@router.get("/catalog")
async def list_catalog(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    rows = (await session.scalars(select(CatalogItem).order_by(CatalogItem.category, CatalogItem.name))).all()
    return [catalog_out(c) for c in rows]


class CatalogIn(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    category: str = Field("", max_length=100)
    spec: str = Field("", max_length=1000)
    unit_price: int | None = Field(None, ge=1, le=10_000_000_000)
    sdp_product: str = Field("", max_length=300)
    vendor: str = Field("", max_length=200)
    quoted_at: date | None = None
    active: bool = True


async def _save_catalog(
    session: AsyncSession, user: CurrentUser, c: CatalogItem, body: CatalogIn, before: dict[str, Any] | None
) -> dict[str, Any]:
    c.name, c.category, c.spec = body.name.strip(), body.category.strip(), body.spec.strip()
    c.sdp_product = body.sdp_product.strip()
    c.unit_price, c.vendor, c.quoted_at, c.active = (
        body.unit_price,
        body.vendor.strip(),
        body.quoted_at,
        body.active,
    )
    c.updated_by, c.updated_at = user.upn, datetime.now(UTC)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise AppError("Đã có thiết bị cùng tên trong bảng giá.", code="duplicate_name") from exc
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.catalog_saved",
        target_type="catalog_item",
        target_id=str(c.id),
        target_label=c.name,
        before=before,
        after={"unit_price": c.unit_price, "vendor": c.vendor, "active": c.active},
    )
    # A new price changes the value of open POs: re-pack them under the limit.
    await pos.repack(session, await _settings(session))
    await session.commit()
    return catalog_out(c)


@router.post("/catalog", status_code=201)
async def create_catalog_item(
    body: CatalogIn, user: CurrentUser = Depends(catalog_manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    c = CatalogItem(id=uuid.uuid4())
    session.add(c)
    return await _save_catalog(session, user, c, body, None)


@router.put("/catalog/{item_id}")
async def update_catalog_item(
    item_id: uuid.UUID,
    body: CatalogIn,
    user: CurrentUser = Depends(catalog_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    c = await session.get(CatalogItem, item_id, with_for_update=True)
    if c is None:
        raise NotFound("Không tìm thấy thiết bị trong bảng giá.")
    before = {"unit_price": c.unit_price, "vendor": c.vendor, "active": c.active}
    return await _save_catalog(session, user, c, body, before)


# ---------------------------------------------------------------- purchase orders
async def po_out(session: AsyncSession, po: PurchaseOrder) -> dict[str, Any]:
    items = await pos.po_items(session, po)
    rows = await _full(session, [p for p, _ in items])
    return {
        "id": str(po.id),
        "number": po.number,
        "status": po.status,
        "total": po.total,
        "limit": po.limit,
        "over_limit": po.total > po.limit,
        "created_at": po.created_at,
        "closed_at": po.closed_at,
        "closed_by": po.closed_by,
        "items": rows,
    }


@router.get("/pos")
async def list_pos(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = await _settings(session)
    open_rows = (
        await session.scalars(
            select(PurchaseOrder).where(PurchaseOrder.status == "open").order_by(PurchaseOrder.created_at)
        )
    ).all()
    closed = (
        await session.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.status == "closed")
            .order_by(PurchaseOrder.closed_at.desc())
            .limit(50)
        )
    ).all()
    waiting = await session.scalar(select(func.count()).where(PurchaseRequest.status == "proposed"))
    return {
        "limit": settings.po_limit_vnd,
        "open": [await po_out(session, o) for o in open_rows],
        "closed": [await po_out(session, o) for o in closed],
        "proposed_waiting": waiting or 0,
    }


class CloseIn(BaseModel):
    version_total: int = Field(ge=0)  # total the supervisor saw; refuses if the PO changed meanwhile


@router.post("/pos/{po_id}/close")
async def close_po(
    po_id: uuid.UUID,
    body: CloseIn,
    user: CurrentUser = Depends(po_close),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await pos.repack(session, await _settings(session))  # takes the PO lock, brings totals up to date
    po = await session.get(PurchaseOrder, po_id, with_for_update=True)
    if po is None or po.status != "open":
        raise NotFound("PO không còn mở (đã chốt hoặc đã được gom lại).")
    if po.total != body.version_total:
        raise AppError(
            "Giá trị PO vừa thay đổi (giá hoặc đề xuất mới) – xem lại rồi chốt.", code="po_changed"
        )
    items = await pos.po_items(session, po)
    if not items:
        raise AppError("PO chưa có hạng mục.", code="po_empty")
    now = datetime.now(UTC)
    po.number = await pos.next_number(session, now)
    po.status, po.closed_at, po.closed_by = "closed", now, user.upn
    for p, c in items:
        p.unit_price = pos.price_of(p, c)  # freeze the price
        p.status, p.updated_at = "ordered", now
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.po_closed",
        target_type="po",
        target_id=str(po.id),
        target_label=po.number,
        after={"total": po.total, "items": len(items), "limit": po.limit},
    )
    await session.commit()
    return await po_out(session, po)


@router.get("/pos/{po_id}/email")
async def po_email(
    po_id: uuid.UUID, _: CurrentUser = Depends(po_close), session: AsyncSession = Depends(get_session)
) -> Response:
    po = await session.get(PurchaseOrder, po_id)
    if po is None or po.status != "closed":
        raise NotFound("Chỉ tạo email nháp cho PO đã chốt.")
    data = await pos.build_eml(session, await _settings(session), po)
    return Response(
        data,
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="{po.number}.eml"'},
    )


# ---------------------------------------------------------------- operator portal
@router.get("/my-work")
async def my_work(
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """The signed-in operator's own SDP tasks by phase, recent completions and purchase suggestions."""
    me = await _my_technician(session, user)
    if me is None:
        return {"linked": False}
    now = datetime.now(UTC)
    mine = Ticket.technician_id == me.id
    counts = dict(
        (
            await session.execute(
                select(Ticket.phase, func.count()).where(mine, Ticket.state == "open").group_by(Ticket.phase)
            )
        ).all()
    )
    overdue = await session.scalar(
        select(func.count()).where(mine, Ticket.state == "open", Ticket.due_by < now)
    )
    week = now - timedelta(days=7)
    done = (
        await session.scalars(
            select(Ticket)
            .where(mine, Ticket.state == "done", Ticket.completed_at >= week)
            .order_by(Ticket.completed_at.desc())
            .limit(10)
        )
    ).all()
    open_rows = (
        await session.scalars(
            select(Ticket)
            .where(mine, Ticket.state == "open")
            .order_by(Ticket.due_by.asc().nulls_last())
            .limit(200)
        )
    ).all()
    has_po = exists().where(PurchaseRequest.ticket_id == Ticket.id, PurchaseRequest.status != "rejected")
    suggestions = (
        await session.scalars(
            select(Ticket).where(
                mine,
                Ticket.state == "open",
                Ticket.device_need.is_not(None),
                Ticket.po_dismissed.is_(False),
                ~has_po,
            )
        )
    ).all()
    return {
        "linked": True,
        "technician": me.name,
        "counts": {
            "pending": counts.get("pending", 0),
            "in_progress": counts.get("in_progress", 0),
            "on_hold": counts.get("on_hold", 0),
            "overdue": overdue or 0,
            "done_7d": len(done),
        },
        "open": [ticket_out(t, now) for t in open_rows],
        "done": [ticket_out(t, now) for t in done],
        "suggestions": [ticket_out(t, now) for t in suggestions],
    }
