"""Device purchase proposals: suggested from SDP requests about replacing, upgrading or repairing a
device, kept in infra-ops and exported for the purchasing team (nothing is written to SDP)."""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import String, exists, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.models import AuditLog
from app.core.audit.service import Actor, record
from app.core.auth.deps import CurrentUser, require
from app.core.auth.models import User
from app.core.config import get_settings
from app.core.connections.models import ConnectionProfile
from app.core.db import get_session
from app.core.errors import AppError, NotFound
from app.core.storage.models import StoredFile
from app.core.storage.service import check_size, get_storage, read_file, safe_filename, save_file, sniff
from app.core.workflow.engine import create_job, start_job
from app.core.workflow.models import Job
from app.modules.workload import handover
from app.modules.workload import po as pos
from app.modules.workload.api import VIEW_ALL, _my_technician, ticket_out
from app.modules.workload.models import (
    CatalogItem,
    PurchaseEvidence,
    PurchaseOrder,
    PurchaseQuote,
    PurchaseRequest,
    Supplier,
    Ticket,
)
from app.modules.workload.quotes import detect_supplier, match_prices, needs_quotes, pdf_text, quote_problem
from app.modules.workload.settings import MODULE_ID, WorkloadSettings
from app.modules.workload.settings import load as load_module_settings

router = APIRouter(tags=["workload"])
view = require("workload.tickets.view")
create = require("workload.purchase.create")
manage = require("workload.purchase.manage")
receiver = require("workload.purchase.receive")
HANDOVER_MIME = {"application/pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
catalog_manage = require("workload.catalog.manage")
po_close = require("workload.po.close")
PRICE = "workload.price.view"
price_view = require(PRICE)
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
    quotes: list[PurchaseQuote] | None = None,
    settings: WorkloadSettings | None = None,
) -> dict[str, Any]:
    price = pos.price_of(p, cat)
    by_quote = bool(cat and settings and needs_quotes(cat.category, settings.quote_categories))
    return {
        "id": str(p.id),
        "ticket_id": p.ticket_id,
        "ticket_display_id": p.ticket_display_id,
        "source": p.source,
        "source_ref": p.source_ref,
        "employee": p.employee,
        "kind": p.kind,
        "item": p.item,
        "catalog_item": {"id": str(cat.id), "name": cat.name, "category": cat.category} if cat else None,
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
        "quotes": [
            {
                "id": str(q.id),
                "vendor": q.vendor,
                "unit_price": q.unit_price,
                "out_of_stock": q.out_of_stock,
                "filename": q.filename,
                "mime": q.mime,
                "lowest": q.unit_price is not None and q.unit_price == p.quote_price,
                "created_by": q.created_by,
            }
            for q in quotes or []
        ],
        # PC/Laptop-type items: priced from vendor quotes; problem = what blocks approval (None = ok).
        "quote_rule": {
            "required": True,
            "problem": quote_problem(
                [q.unit_price for q in quotes or [] if q.unit_price is not None],
                sum(q.out_of_stock for q in quotes or []),
                settings.quote_threshold_vnd,
                settings.quotes_required,
            ),
            "threshold": settings.quote_threshold_vnd,
            "count": settings.quotes_required,
        }
        if by_quote and settings
        else None,
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


HIDDEN = {"unit_price": None, "amount": None, "po": None, "quotes": [], "quote_rule": None}


async def _full(
    session: AsyncSession, rows: list[PurchaseRequest], user: CurrentUser | None = None
) -> list[dict[str, Any]]:
    """Serialize proposals with catalog item, evidence, quotes, PO and receive job (no N+1).

    Prices, quotes and POs are only for price viewers (supervisors); operators request devices only."""
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
    quotes: dict[uuid.UUID, list[PurchaseQuote]] = {}
    if rows:
        for q in (
            await session.scalars(
                select(PurchaseQuote)
                .where(PurchaseQuote.purchase_id.in_([p.id for p in rows]))
                .order_by(PurchaseQuote.unit_price)
            )
        ).all():
            quotes.setdefault(q.purchase_id, []).append(q)
    settings = await load_module_settings(session)
    upns = {p.created_by for p in rows}
    names = dict((await session.execute(select(User.upn, User.display_name).where(User.upn.in_(upns)))).all()) if upns else {}  # fmt: skip
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
    result = [
        out(
            p,
            cats.get(p.catalog_item_id),  # type: ignore[arg-type]
            ev.get(p.id, []),
            po_rows.get(p.po_id),  # type: ignore[arg-type]
            jobs.get(str(p.id)),
            quotes.get(p.id, []),
            settings,
        )
        for p in rows
    ]
    for r, p in zip(result, rows, strict=True):
        r["created_by_name"] = names.get(p.created_by) or p.created_by
    if user is not None and not user.has(PRICE):
        result = [{**r, **HIDDEN} for r in result]
    return result


async def _one(session: AsyncSession, p: PurchaseRequest, user: CurrentUser | None = None) -> dict[str, Any]:
    return (await _full(session, [p], user))[0]


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
    return {"items": await _full(session, list(rows), user), "counts": counts}


async def _own_ticket(session: AsyncSession, user: CurrentUser, ticket_id: str) -> Ticket:
    """The ticket, if the user may act on it: without view_all only tickets assigned to them."""
    ticket = await session.get(Ticket, ticket_id)
    if ticket is not None and not user.has(VIEW_ALL):
        me = await _my_technician(session, user)
        if me is None or ticket.technician_id != me.id:
            ticket = None
    if ticket is None:
        raise NotFound("Không tìm thấy request.")
    return ticket


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
        display = (await _own_ticket(session, user, body.ticket_id)).display_id
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
    return await _one(session, p, user)


class StatusIn(BaseModel):
    status: Status
    note: str = Field("", max_length=1000)
    # The supervisor can link (or fix) the price-list item when approving.
    catalog_item_id: uuid.UUID | None = None


async def _change_status(
    session: AsyncSession, user: CurrentUser, p: PurchaseRequest, body: StatusIn
) -> bool:
    """Validate and apply one manual status change (audited). Returns True when POs must be re-packed."""
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
        if cat is None:
            raise AppError("Chọn thiết bị trong bảng giá trước khi duyệt.", code="price_required")
        settings = await load_module_settings(session)
        if needs_quotes(cat.category, settings.quote_categories):
            rows = (
                await session.scalars(select(PurchaseQuote).where(PurchaseQuote.purchase_id == p.id))
            ).all()
            problem = quote_problem(
                [q.unit_price for q in rows if q.unit_price is not None],
                sum(q.out_of_stock for q in rows),
                settings.quote_threshold_vnd,
                settings.quotes_required,
            )
            if problem:
                raise AppError(problem, code="quotes_required")
        elif not cat.unit_price:
            raise AppError(
                "Chưa có đơn giá: cập nhật giá thiết bị trong Bảng giá (từ file báo giá của đối tác).",
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
    return "approved" in (before, body.status)


@router.patch("/purchases/{purchase_id}")
async def update_purchase(
    purchase_id: uuid.UUID,
    body: StatusIn,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await pos.lock_pos(session)  # PO lock before the row lock, in the same order as close_po (no deadlock)
    p = await session.get(PurchaseRequest, purchase_id, with_for_update=True)
    if p is None:
        raise NotFound("Không tìm thấy đề xuất.")
    if await _change_status(session, user, p, body):
        await pos.repack(session, await load_module_settings(session))
    await session.commit()
    await session.refresh(p)
    return await _one(session, p, user)


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    status: Literal["approved", "rejected"]
    note: str = Field("", max_length=1000)


@router.post("/purchases/bulk")
async def bulk_status(
    body: BulkIn, user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Approve or reject many proposals at once. Each one is checked like a single change (evidence,
    price, quotes); those that fail are reported and left unchanged, the others are applied."""
    done: list[str] = []
    failed: dict[str, str] = {}
    repack = False
    await pos.lock_pos(session)  # PO lock before the row locks, in the same order as close_po
    for pid in dict.fromkeys(body.ids):
        try:
            async with session.begin_nested():
                p = await session.get(PurchaseRequest, pid, with_for_update=True)
                if p is None:
                    raise NotFound("Không tìm thấy đề xuất.")
                repack |= await _change_status(session, user, p, StatusIn(status=body.status, note=body.note))
            done.append(str(pid))
        except AppError as exc:
            failed[str(pid)] = exc.message
    if repack:
        await pos.repack(session, await load_module_settings(session))
    await session.commit()
    return {"done": done, "failed": failed}


class UnitIn(BaseModel):
    serial: str = Field(min_length=2, max_length=100)
    asset_tag: str = Field("", max_length=100)


class ReceiveIn(BaseModel):
    units: list[UnitIn] = Field(default_factory=list, max_length=500)
    note: str = Field("", max_length=1000)


@router.get("/purchases/receive-target")
async def receive_target(
    _: CurrentUser = Depends(receiver), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Which SDP connection "Nhập kho SDP" will write to (shown before the user confirms)."""
    settings = await load_module_settings(session)
    stmt = select(ConnectionProfile).where(ConnectionProfile.system_type == "sdp")
    if settings.sdp_profile_id:
        stmt = stmt.where(ConnectionProfile.id == settings.sdp_profile_id)
    else:
        stmt = stmt.where(ConnectionProfile.environment == "prod", ConnectionProfile.enabled.is_(True))
    prof = (await session.scalars(stmt.order_by(ConnectionProfile.created_at).limit(1))).first()
    return {
        "connection": {"name": prof.name, "environment": prof.environment, "enabled": prof.enabled}
        if prof
        else None,
        "state": settings.asset_state_in_store,
        "asset_tag": {"prefix": settings.asset_tag_prefix, "codes": settings.asset_tag_codes},
    }


@router.get("/purchases/receivable")
async def receivable(
    user: CurrentUser = Depends(receiver), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Closed POs with goods still to receive – without prices (operators unpack the boxes)."""
    closed = (
        await session.scalars(
            select(PurchaseOrder)
            .where(
                PurchaseOrder.status == "closed",
                exists().where(
                    PurchaseRequest.po_id == PurchaseOrder.id, PurchaseRequest.status == "ordered"
                ),
            )
            .order_by(PurchaseOrder.closed_at.desc())
            .limit(50)
        )
    ).all()
    items = (
        await session.scalars(
            select(PurchaseRequest)
            .where(PurchaseRequest.po_id.in_([po.id for po in closed]))
            .order_by(PurchaseRequest.approved_at, PurchaseRequest.created_at)
        )
    ).all()
    by_po: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for p, row in zip(items, await _full(session, list(items), user), strict=True):
        by_po.setdefault(p.po_id, []).append(row)  # type: ignore[arg-type]
    out = [
        {"id": str(po.id), "number": po.number, "closed_at": po.closed_at, "items": by_po.get(po.id, [])}
        for po in closed
    ]
    return {"closed": out}


@router.post("/purchases/receive/extract")
async def extract_serials(file: UploadFile, _: CurrentUser = Depends(receiver)) -> dict[str, Any]:
    """Serials (and asset tags) read from the vendor's handover document, to pre-fill the receive form.
    Nothing is stored or written; the person reviews every value."""
    data, mime = await _read_upload(file, HANDOVER_MIME)
    rows = await asyncio.to_thread(handover.read_handover, data, mime)
    if not rows:
        raise AppError(
            "Không tìm thấy cột/nhãn Serial trong file – kiểm tra file hoặc nhập tay.", code="no_serials"
        )
    return {"rows": rows}


# Serials of other purchases: already received, or in a receive job that is not cancelled.
_USED_SERIAL = text(
    """
    SELECT u->>'serial' FROM workload_purchase_request p, jsonb_array_elements(p.received_assets) u
    WHERE p.id <> :pid AND lower(u->>'serial') = ANY(:serials)
    UNION ALL
    SELECT u->>'serial' FROM core_job j, jsonb_array_elements(j.params->'units') u
    WHERE j.workflow_key = 'workload.receive' AND j.status <> 'cancelled'
      AND j.external_ref <> :ref AND lower(u->>'serial') = ANY(:serials)
    LIMIT 1
    """
)


async def _serial_used(session: AsyncSession, purchase_id: uuid.UUID, serials: list[str]) -> str | None:
    # Serialise receives so two requests cannot take the same serial at once.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('workload.receive'))"))
    return await session.scalar(
        _USED_SERIAL, {"pid": purchase_id, "ref": str(purchase_id), "serials": [x.lower() for x in serials]}
    )


@router.post("/purchases/{purchase_id}/receive")
async def receive(
    purchase_id: uuid.UUID,
    body: ReceiveIn,
    user: CurrentUser = Depends(receiver),
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
        return await _one(session, p, user)

    serials = [u.serial.strip() for u in body.units]
    if len(serials) != p.quantity:
        raise AppError(f"Nhập đủ serial cho {p.quantity} thiết bị.", code="units_mismatch")
    if len({x.lower() for x in serials}) != len(serials):
        raise AppError("Serial bị trùng.", code="duplicate_serial")
    used = await _serial_used(session, p.id, serials)
    if used:
        raise AppError(f"Serial {used} đã được nhận cho đề xuất khác.", code="serial_used")
    settings = await load_module_settings(session)
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
    return await _one(session, p, user)


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
    return await _one(session, p, user)


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
    return await _one(session, p, user)


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
    ticket = await _own_ticket(session, user, ticket_id)
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


# ---------------------------------------------------------------- vendor quotes (3-quote process)
MAX_QUOTES = 10


async def _set_quote_price(session: AsyncSession, p: PurchaseRequest) -> None:
    await session.flush()
    p.quote_price = await session.scalar(
        select(func.min(PurchaseQuote.unit_price)).where(PurchaseQuote.purchase_id == p.id)
    )


async def _quote_target(session: AsyncSession, purchase_id: uuid.UUID) -> PurchaseRequest:
    p = await session.get(PurchaseRequest, purchase_id, with_for_update=True)
    if p is None:
        raise NotFound("Không tìm thấy đề xuất.")
    if p.status != "proposed":
        raise AppError("Chỉ sửa báo giá khi đề xuất chưa được duyệt.", code="quotes_locked")
    return p


QUOTE_MIME = {"application/pdf"}
QUOTE_FILE_READ = "workload.quote_file_read"  # audit action; also finds the files to clean up
STOCK_MIME = {"image/png", "image/jpeg", "application/pdf"}


async def _suppliers(session: AsyncSession) -> list[Supplier]:
    return list(
        (await session.scalars(select(Supplier).where(Supplier.active).order_by(Supplier.name))).all()
    )


async def _read_upload(file: UploadFile, allowed: set[str]) -> tuple[bytes, str]:
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    check_size(data)
    return data, sniff(data, allowed)


async def _own_upload(
    session: AsyncSession, user: CurrentUser, file_id: uuid.UUID, allowed: set[str]
) -> StoredFile:
    stored = await session.get(StoredFile, file_id)
    if stored is None or stored.mime not in allowed or stored.uploaded_by != user.upn:
        raise NotFound("Không tìm thấy file đã tải lên.")
    return stored


async def _save_read_quote(
    session: AsyncSession, user: CurrentUser, data: bytes, filename: str | None
) -> StoredFile:
    """Keep a quote PDF read by "Đọc báo giá" so the confirm step can attach it (file_id).
    Unattached ones are removed after a week by the workload.quote_files_cleanup task."""
    stored = await save_file(
        session, data, safe_filename(filename or "bao-gia.pdf"), "application/pdf", user.upn
    )
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action=QUOTE_FILE_READ,
        target_type="file",
        target_id=str(stored.id),
        target_label=stored.filename,
        after={"size": stored.size},
    )
    return stored


QUOTE_FILE_DAYS = 7


async def cleanup_quote_files(session: AsyncSession) -> dict[str, Any]:
    """Daily: delete quote PDFs read by "Đọc báo giá" over a week ago and never attached to a quote
    or price-list item (files are recognised by their audit entry, so other uploads are never touched)."""
    read = select(AuditLog.target_id).where(
        AuditLog.module_id == MODULE_ID, AuditLog.action == QUOTE_FILE_READ, AuditLog.target_type == "file"
    )
    rows = (
        await session.scalars(
            select(StoredFile)
            .where(
                StoredFile.created_at < datetime.now(UTC) - timedelta(days=QUOTE_FILE_DAYS),
                StoredFile.id.cast(String).in_(read),
                ~exists().where(PurchaseQuote.file_id == StoredFile.id),
                ~exists().where(CatalogItem.quote_file_id == StoredFile.id),
                ~exists().where(PurchaseEvidence.file_id == StoredFile.id),
            )
            .limit(500)
        )
    ).all()
    if not rows:
        return {"deleted": 0}
    keys = [f.storage_key for f in rows]
    for f in rows:
        await session.delete(f)
    await record(
        session,
        actor=Actor(None, "system"),
        module_id=MODULE_ID,
        action="workload.quote_files_cleaned",
        target_type="file",
        after={"deleted": len(rows), "files": [str(f.id) for f in rows]},
    )
    await session.commit()
    # Bytes go only once the rows are gone for good.
    # shortcut: uses the storage's private path helper; switch to a core delete helper once there is one.
    storage = get_storage()
    for key in keys:
        await asyncio.to_thread(storage._path(key).unlink, missing_ok=True)
    return {"deleted": len(rows)}


@router.post("/purchases/{purchase_id}/quotes/read")
async def read_purchase_quote(
    purchase_id: uuid.UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Recognise the vendor of a quote PDF and read this proposal's unit price (a suggestion to confirm)."""
    p = await _quote_target(session, purchase_id)
    data, _ = await _read_upload(file, QUOTE_MIME)
    text = await asyncio.to_thread(pdf_text, data)
    suppliers = await _suppliers(session)
    sup = detect_supplier(text, [{"keywords": x.keywords, "obj": x} for x in suppliers])
    supplier = sup["obj"] if sup else None
    cat = await session.get(CatalogItem, p.catalog_item_id) if p.catalog_item_id else None
    names = [n for n in {cat.name if cat else "", p.item} if n]
    found = match_prices(
        text, [{"id": n, "name": n} for n in names], position=supplier.price_position if supplier else 0
    )
    stored = await _save_read_quote(session, user, data, file.filename)
    name = stored.filename
    await session.commit()
    best = max(found, key=lambda f: f["score"]) if found else None
    return {
        "file_id": str(stored.id),
        "filename": name,
        "supplier": {"id": str(supplier.id), "name": supplier.name} if supplier else None,
        "unit_price": best["price"] if best else None,
        "line": best["line"] if best else None,
    }


@router.post("/purchases/{purchase_id}/quotes", status_code=201)
async def add_quote(
    purchase_id: uuid.UUID,
    vendor: str = Form(min_length=2, max_length=200),
    unit_price: int | None = Form(None, ge=1, le=10_000_000_000),
    out_of_stock: bool = Form(False),
    supplier_id: uuid.UUID | None = Form(None),
    file_id: uuid.UUID | None = Form(None),
    file: UploadFile | None = File(None),
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """A vendor quote (price + PDF), or "no stock" with a screenshot of the vendor's web store."""
    p = await _quote_target(session, purchase_id)
    count = await session.scalar(select(func.count()).where(PurchaseQuote.purchase_id == p.id))
    if (count or 0) >= MAX_QUOTES:
        raise AppError(f"Tối đa {MAX_QUOTES} báo giá mỗi đề xuất.", code="too_many_quotes")
    allowed = STOCK_MIME if out_of_stock else QUOTE_MIME
    if not out_of_stock and unit_price is None:
        raise AppError("Nhập đơn giá của báo giá.", code="price_required")
    if file is not None:
        data, mime = await _read_upload(file, allowed)
        name = safe_filename(file.filename or ("het-hang.png" if out_of_stock else "bao-gia.pdf"))
        stored = await save_file(session, data, name, mime, user.upn)
    elif file_id is not None:  # PDF already uploaded by /quotes/read
        stored = await _own_upload(session, user, file_id, allowed)
        name, mime = stored.filename, stored.mime
    else:
        raise AppError(
            "Đính kèm ảnh chụp website không có hàng." if out_of_stock else "Đính kèm file báo giá PDF.",
            code="file_required",
        )
    if supplier_id is not None and await session.get(Supplier, supplier_id) is None:
        raise NotFound("Không tìm thấy nhà cung cấp.")
    q = PurchaseQuote(
        purchase_id=p.id,
        vendor=vendor.strip(),
        supplier_id=supplier_id,
        unit_price=None if out_of_stock else unit_price,
        out_of_stock=out_of_stock,
        file_id=stored.id,
        filename=name,
        mime=mime,
        created_by=user.upn,
    )
    session.add(q)
    await _set_quote_price(session, p)
    p.updated_at = datetime.now(UTC)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.quote_added",
        target_type="purchase",
        target_id=str(p.id),
        target_label=p.item,
        after={"vendor": q.vendor, "unit_price": q.unit_price, "out_of_stock": out_of_stock, "filename": name,
               "lowest": p.quote_price},
    )  # fmt: skip
    await session.commit()
    return await _one(session, p, user)


@router.delete("/purchases/{purchase_id}/quotes/{quote_id}")
async def delete_quote(
    purchase_id: uuid.UUID,
    quote_id: uuid.UUID,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    p = await _quote_target(session, purchase_id)
    q = await session.get(PurchaseQuote, quote_id)
    if q is None or q.purchase_id != p.id:
        raise NotFound("Không tìm thấy báo giá.")
    await session.delete(q)
    await _set_quote_price(session, p)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.quote_removed",
        target_type="purchase",
        target_id=str(p.id),
        target_label=p.item,
        before={"vendor": q.vendor, "unit_price": q.unit_price, "out_of_stock": q.out_of_stock},
    )
    await session.commit()
    return await _one(session, p, user)


def _pdf_response(data: bytes, filename: str, mime: str = "application/pdf") -> Response:
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/purchases/{purchase_id}/quotes/{quote_id}")
async def quote_file(
    purchase_id: uuid.UUID,
    quote_id: uuid.UUID,
    _: CurrentUser = Depends(price_view),
    session: AsyncSession = Depends(get_session),
) -> Response:
    q = await session.get(PurchaseQuote, quote_id)
    if q is None or q.purchase_id != purchase_id:
        raise NotFound("Không tìm thấy báo giá.")
    _, data = await read_file(session, q.file_id)
    return _pdf_response(data, q.filename, q.mime)


@router.get("/purchases/export")
async def export_purchases(
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> Response:
    rows = await _full(
        session, list((await session.scalars(_scoped(user).order_by(PurchaseRequest.created_at))).all()), user
    )
    priced = user.has(PRICE)
    keep = (lambda r: r) if priced else (lambda r: [v for i, v in enumerate(r) if i not in (6, 7, 10)])
    wb = Workbook()
    ws = wb.active
    ws.title = "De xuat mua thiet bi"
    ws.append(
        keep(
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
    )
    for p in rows:
        ws.append(
            keep(
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
        "quote_file": bool(c.quote_file_id),
        "vendor": c.vendor,
        "quoted_at": c.quoted_at,
        "active": c.active,
        "updated_by": c.updated_by,
        "updated_at": c.updated_at,
    }


@router.get("/catalog")
async def list_catalog(
    user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    rows = (await session.scalars(select(CatalogItem).order_by(CatalogItem.category, CatalogItem.name))).all()
    if user.has(PRICE):
        cats = (await load_module_settings(session)).quote_categories
        return [{**catalog_out(c), "needs_quotes": needs_quotes(c.category, cats)} for c in rows]
    # Operators pick a device by name only.
    return [{k: catalog_out(c)[k] for k in ("id", "name", "category", "spec", "active")} for c in rows]


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
    await pos.repack(session, await load_module_settings(session))
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


# PDF price import: upload a vendor quote, review the matched prices, then apply the confirmed ones.
@router.post("/catalog/quote-pdf")
async def read_quote_pdf(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(catalog_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    data, _ = await _read_upload(file, QUOTE_MIME)
    text = await asyncio.to_thread(pdf_text, data)
    items = [
        catalog_out(c) for c in (await session.scalars(select(CatalogItem).where(CatalogItem.active))).all()
    ]
    sup = detect_supplier(text, [{"keywords": x.keywords, "obj": x} for x in await _suppliers(session)])
    supplier = sup["obj"] if sup else None
    stored = await _save_read_quote(session, user, data, file.filename)
    name = stored.filename
    await session.commit()
    return {
        "file_id": str(stored.id),
        "filename": name,
        "supplier": {"id": str(supplier.id), "name": supplier.name} if supplier else None,
        "suggestions": match_prices(text, items, position=supplier.price_position if supplier else 0),
    }


class PriceIn(BaseModel):
    id: uuid.UUID
    unit_price: int = Field(ge=1, le=10_000_000_000)


class ApplyQuoteIn(BaseModel):
    file_id: uuid.UUID
    vendor: str = Field(min_length=2, max_length=200)
    quoted_at: date | None = None
    prices: list[PriceIn] = Field(min_length=1, max_length=500)


@router.post("/catalog/apply-quote")
async def apply_quote(
    body: ApplyQuoteIn,
    user: CurrentUser = Depends(catalog_manage),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    stored = await session.get(StoredFile, body.file_id)
    if stored is None or stored.mime != "application/pdf" or stored.uploaded_by != user.upn:
        raise NotFound("Không tìm thấy file báo giá đã tải lên.")
    now = datetime.now(UTC)
    changed = []
    for row in body.prices:
        c = await session.get(CatalogItem, row.id, with_for_update=True)
        if c is None:
            raise NotFound("Không tìm thấy thiết bị trong bảng giá.")
        before = {"unit_price": c.unit_price, "vendor": c.vendor}
        c.unit_price, c.vendor, c.quoted_at, c.quote_file_id = (
            row.unit_price,
            body.vendor.strip(),
            body.quoted_at,
            stored.id,
        )
        c.updated_by, c.updated_at = user.upn, now
        await record(
            session,
            actor=user.actor,
            module_id=MODULE_ID,
            action="workload.catalog_saved",
            target_type="catalog_item",
            target_id=str(c.id),
            target_label=c.name,
            before=before,
            after={"unit_price": c.unit_price, "vendor": c.vendor, "quote_file": stored.filename},
        )
        changed.append(catalog_out(c))
    await pos.repack(session, await load_module_settings(session))
    await session.commit()
    return changed


@router.get("/catalog/{item_id}/quote-file")
async def catalog_quote_file(
    item_id: uuid.UUID, _: CurrentUser = Depends(price_view), session: AsyncSession = Depends(get_session)
) -> Response:
    c = await session.get(CatalogItem, item_id)
    if c is None or c.quote_file_id is None:
        raise NotFound("Thiết bị chưa có file báo giá.")
    stored, data = await read_file(session, c.quote_file_id)
    return _pdf_response(data, stored.filename)


# ---------------------------------------------------------------- suppliers (quote recognition)
def supplier_out(x: Supplier) -> dict[str, Any]:
    return {
        "id": str(x.id),
        "name": x.name,
        "keywords": x.keywords,
        "price_position": x.price_position,
        "website": x.website,
        "active": x.active,
        "updated_by": x.updated_by,
        "updated_at": x.updated_at,
    }


@router.get("/suppliers")
async def list_suppliers(
    _: CurrentUser = Depends(price_view), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    return [supplier_out(x) for x in (await session.scalars(select(Supplier).order_by(Supplier.name))).all()]


class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    price_position: int = Field(0, ge=0, le=20)
    website: str = Field("", max_length=300)
    active: bool = True


async def _save_supplier(
    session: AsyncSession, user: CurrentUser, x: Supplier, body: SupplierIn, before: dict[str, Any] | None
) -> dict[str, Any]:
    x.name, x.website, x.active, x.price_position = (
        body.name.strip(),
        body.website.strip(),
        body.active,
        body.price_position,
    )
    x.keywords = [k.strip() for k in body.keywords if len(k.strip()) >= 3]
    if body.active and not x.keywords:
        raise AppError(
            "Nhập ít nhất một từ khoá nhận diện (≥ 3 ký tự), vd. mã số thuế.", code="keywords_required"
        )
    x.updated_by, x.updated_at = user.upn, datetime.now(UTC)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise AppError("Đã có nhà cung cấp cùng tên.", code="duplicate_name") from exc
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="workload.supplier_saved",
        target_type="supplier",
        target_id=str(x.id),
        target_label=x.name,
        before=before,
        after={"keywords": x.keywords, "price_position": x.price_position, "active": x.active},
    )
    await session.commit()
    return supplier_out(x)


@router.post("/suppliers", status_code=201)
async def create_supplier(
    body: SupplierIn,
    user: CurrentUser = Depends(catalog_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    x = Supplier(id=uuid.uuid4())
    session.add(x)
    return await _save_supplier(session, user, x, body, None)


@router.put("/suppliers/{supplier_id}")
async def update_supplier(
    supplier_id: uuid.UUID,
    body: SupplierIn,
    user: CurrentUser = Depends(catalog_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    x = await session.get(Supplier, supplier_id, with_for_update=True)
    if x is None:
        raise NotFound("Không tìm thấy nhà cung cấp.")
    before = {"keywords": x.keywords, "price_position": x.price_position, "active": x.active}
    return await _save_supplier(session, user, x, body, before)


# ---------------------------------------------------------------- purchase orders
async def po_out(
    session: AsyncSession, po: PurchaseOrder, rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    if rows is None:
        rows = await _full(session, [p for p, _ in await pos.po_items(session, po)])
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
    _: CurrentUser = Depends(price_view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = await load_module_settings(session)
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
    # All items of the listed POs in one pass (po_out per PO would run ~8 queries each).
    po_ids = [o.id for o in (*open_rows, *closed)]
    items = (
        await session.scalars(
            select(PurchaseRequest)
            .where(PurchaseRequest.po_id.in_(po_ids))
            .order_by(PurchaseRequest.approved_at, PurchaseRequest.created_at)
        )
    ).all()
    by_po: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for p, row in zip(items, await _full(session, list(items)), strict=True):
        by_po.setdefault(p.po_id, []).append(row)  # type: ignore[arg-type]
    return {
        "limit": settings.po_limit_vnd,
        "email_to": settings.po_email_to,
        "email_cc": settings.po_email_cc,
        "open": [await po_out(session, o, by_po.get(o.id, [])) for o in open_rows],
        "closed": [await po_out(session, o, by_po.get(o.id, [])) for o in closed],
        "proposed_waiting": waiting or 0,
    }


class CloseIn(BaseModel):
    # What the supervisor saw; refuses if the PO changed meanwhile (same total with other items too).
    version_total: int = Field(ge=0)
    item_ids: list[uuid.UUID] = Field(max_length=1000)


@router.post("/pos/{po_id}/close")
async def close_po(
    po_id: uuid.UUID,
    body: CloseIn,
    user: CurrentUser = Depends(po_close),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await pos.repack(
        session, await load_module_settings(session)
    )  # takes the PO lock, brings totals up to date
    po = await session.get(PurchaseOrder, po_id, with_for_update=True)
    if po is None or po.status != "open":
        raise NotFound("PO không còn mở (đã chốt hoặc đã được gom lại).")
    items = await pos.po_items(session, po)
    if po.total != body.version_total or {p.id for p, _ in items} != set(body.item_ids):
        raise AppError(
            "Giá trị PO vừa thay đổi (giá hoặc đề xuất mới) – xem lại rồi chốt.", code="po_changed"
        )
    if not items:
        raise AppError("PO chưa có hạng mục.", code="po_empty")
    if any(pos.price_of(p, c) is None for p, c in items):
        raise AppError(
            "Có hạng mục chưa có đơn giá – cập nhật Bảng giá trước khi chốt.", code="price_required"
        )
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
    data = await pos.build_eml(session, await load_module_settings(session), po)
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
