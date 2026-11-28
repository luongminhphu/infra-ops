"""Year-end IT asset stocktake (kiểm kê) with Hành chánh / Kế toán: book from SDP, count on site, handle differences."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.branding.service import logo_bytes
from app.core.db import get_session
from app.core.documents import service as documents
from app.core.documents.models import Document
from app.core.errors import AppError, Conflict, NotFound, PermissionDenied
from app.core.storage.service import check_size, sniff
from app.core.timeutil import fmt_date, today_vn
from app.modules.lifecycle.attendance_input import HEADER_ID, _norm_id
from app.modules.lifecycle.fa_register import (
    KINDS,
    MAX_FILE_BYTES,
    XLSX_MIME,
    Parsed,
    jsonable,
    parse_register,
    reconcile_registers,
    register_totals,
)
from app.modules.lifecycle.models import (
    FaRegisterRow,
    Snapshot,
    Stocktake,
    StocktakeArea,
    StocktakeItem,
    StocktakeZone,
)
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import MODULE_ID
from app.modules.lifecycle.settings import load as load_settings
from app.modules.lifecycle.stocktake import (
    ALLOWED_RESOLUTIONS,
    CONDITION_LABEL,
    COUNT_LABEL,
    DEFAULT_RESOLUTIONS,
    RESOLUTION_LABEL,
    Condition,
    Resolution,
    area_for,
    book_item,
    diff_kind,
    difference,
    in_book,
    matches,
    summary,
    to_xlsx,
    zone_progress,
)

router = APIRouter(tags=["lifecycle"])
view_perm = require("lifecycle.stocktake.view")
count_perm = require("lifecycle.stocktake.count")
manage_perm = require("lifecycle.stocktake.manage")
assign_perm = require("lifecycle.stocktake.assign")
# The whole picture (every zone, differences, areas, Excel files). Operators only walk their floors.
view_all_perm = require("lifecycle.stocktake.view_all")
# Accounting figures (nguyên giá, giá trị còn lại): never part of _item_out / GET stocktake.
# Money figures: the finance permission AND sight of the whole stocktake (least privilege: never only one of them).
finance_perm = require("lifecycle.stocktake.finance", "lifecycle.stocktake.view_all")

STATUS_LABEL = {"counting": "Đang kiểm", "reviewing": "Đang xử lý chênh lệch", "closed": "Đã đóng"}


def _item_out(i: StocktakeItem, rules_version: int = 2) -> dict[str, Any]:
    out = {
        "id": i.id,
        "asset_id": i.asset_id,
        "name": i.name,
        "product_type": i.product_type,
        "asset_tag": i.asset_tag,
        "serial": i.serial,
        "fa_code": i.fa_code,
        "state": i.state,
        "user_name": i.user_name,
        "employee_id": i.employee_id,
        "department": i.department,
        "site": i.site,
        "zone_id": str(i.zone_id) if i.zone_id else None,
        "count": i.count,
        "condition": i.condition,
        "seen_at": i.seen_at,
        "note": i.note,
        "counted_by": i.counted_by,
        "counted_at": i.counted_at.isoformat() if i.counted_at else None,
        "resolution": i.resolution,
        "resolution_note": i.resolution_note,
        "resolved_by": i.resolved_by,
        "resolution_suggested": i.resolution_suggested,
    }
    out["difference"] = difference(out, rules_version)
    return out


def _stocktake_out(s: Stocktake) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "cutoff": s.cutoff.isoformat(),
        "status": s.status,
        "status_label": STATUS_LABEL[s.status],
        "rules_version": s.rules_version,
        "source_synced_at": s.source_synced_at.isoformat() if s.source_synced_at else None,
        "created_by": s.created_by,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "closed_by": s.closed_by,
        "closed_at": s.closed_at.isoformat() if s.closed_at else None,
    }


async def _get(session: AsyncSession, stocktake_id: uuid.UUID, *, lock: bool = False) -> Stocktake:
    stmt = select(Stocktake).where(Stocktake.id == stocktake_id)
    s = await session.scalar(stmt.with_for_update() if lock else stmt)
    if s is None:
        raise NotFound("Không tìm thấy kỳ kiểm kê.")
    return s


def _zone_out(z: StocktakeZone) -> dict[str, Any]:
    return {
        "id": str(z.id),
        "name": z.name,
        "is_store": z.is_store,
        "assignee_user_id": str(z.assignee_user_id) if z.assignee_user_id else None,
        "assignee_name": z.assignee_name,
    }


async def _zones(session: AsyncSession, stocktake_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(StocktakeZone)
        .where(StocktakeZone.stocktake_id == stocktake_id)
        .order_by(StocktakeZone.position)
    )
    return [_zone_out(z) for z in rows.all()]


async def _matching(session: AsyncSession, stocktake_id: uuid.UUID, codes: list[str]) -> list[StocktakeItem]:
    """Items matching any code, in id order. Matches in Python on 4 columns, then loads only the hits."""
    rows = await session.execute(
        select(StocktakeItem.id, StocktakeItem.serial, StocktakeItem.asset_tag, StocktakeItem.fa_code)
        .where(StocktakeItem.stocktake_id == stocktake_id)
        .order_by(StocktakeItem.id)
    )
    ids = [
        r.id
        for r in rows
        if any(
            matches({"serial": r.serial, "asset_tag": r.asset_tag, "fa_code": r.fa_code}, c) for c in codes
        )
    ]
    if not ids:
        return []
    return list(
        (
            await session.scalars(
                select(StocktakeItem).where(StocktakeItem.id.in_(ids)).order_by(StocktakeItem.id)
            )
        ).all()
    )


async def _items(session: AsyncSession, stocktake_id: uuid.UUID) -> list[StocktakeItem]:
    return list(
        (
            await session.scalars(
                select(StocktakeItem)
                .where(StocktakeItem.stocktake_id == stocktake_id)
                .order_by(StocktakeItem.id)
            )
        ).all()
    )


async def _reconcile(session: AsyncSession, s: Stocktake, zones: list[dict[str, Any]]) -> dict[str, Any]:
    """Three-way reconciliation of the imported registers (with money: finance callers only)."""
    names = {z["id"]: z["name"] for z in zones}
    items = [
        {**(o := _item_out(i, s.rules_version)), "zone": names.get(o["zone_id"] or "")}
        for i in await _items(session, s.id)
    ]
    regs = (
        await session.scalars(
            select(FaRegisterRow)
            .where(FaRegisterRow.stocktake_id == s.id)
            .order_by(FaRegisterRow.kind, FaRegisterRow.row_no)
        )
    ).all()
    return reconcile_registers([{**_row_out_orm(r), "kind": r.kind} for r in regs], items, s.rules_version)


async def _my_zone_ids(session: AsyncSession, user: CurrentUser, stocktake_id: uuid.UUID) -> set[uuid.UUID]:
    """The zones of this stocktake handed to me."""
    return set(
        await session.scalars(
            select(StocktakeZone.id).where(
                StocktakeZone.stocktake_id == stocktake_id, StocktakeZone.assignee_user_id == user.id
            )
        )
    )


def _clear_resolution(item: StocktakeItem) -> None:
    item.resolution, item.resolution_note, item.resolved_by, item.resolved_at = None, "", None, None
    item.resolution_suggested = False


def _recheck_resolution(item: StocktakeItem, kind_before: str | None, rules_version: int) -> None:
    """A handling agreed for one kind of difference does not carry over when the difference changes kind."""
    if item.resolution and diff_kind(_item_out(item, rules_version), rules_version) != kind_before:
        _clear_resolution(item)


async def _can_touch(session: AsyncSession, user: CurrentUser, s: Stocktake, item: StocktakeItem) -> bool:
    """One rule: without view_all a person touches only the assets of the zones handed to them."""
    return user.has("lifecycle.stocktake.view_all") or (
        item.zone_id is not None and item.zone_id in await _my_zone_ids(session, user, s.id)
    )


async def _walked_zone(
    session: AsyncSession, user: CurrentUser, s: Stocktake, zone_id: uuid.UUID | None
) -> StocktakeZone | None:
    """The zone being walked (scan / extra): in this stocktake, and mine unless I have view_all.
    Without view_all the zone is required, so nothing a person records ends up out of their sight."""
    all_zones = user.has("lifecycle.stocktake.view_all")
    if zone_id is None:
        if not all_zones:
            raise PermissionDenied("Chọn khu bạn được giao kiểm trước khi quét hoặc thêm tài sản.")
        return None
    zone = await session.get(StocktakeZone, zone_id)
    if zone is None or zone.stocktake_id != s.id:
        raise NotFound("Không tìm thấy khu trong kỳ kiểm kê.")
    if not all_zones and zone.id not in await _my_zone_ids(session, user, s.id):
        raise PermissionDenied("Chỉ quét được trong khu bạn được giao kiểm.")
    return zone


def _open_for_count(s: Stocktake) -> None:
    if s.status == "closed":
        raise Conflict("Kỳ kiểm kê đã đóng, không sửa được.", code="stocktake_closed")


def _rule(a: StocktakeArea) -> dict[str, Any]:
    return {
        "name": a.name,
        "is_store": a.is_store,
        "departments": a.departments,
        "employee_ids": a.employee_ids,
    }


def _owner(asset: dict[str, Any], users: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The SDP user of an asset (from the same sync), or None when nobody uses it."""
    if not asset.get("user_id"):
        return None
    return users.get(asset["user_id"]) or {"name": asset.get("user_name")}


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/stocktakes")
async def list_stocktakes(
    _: CurrentUser = Depends(view_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    rows = (await session.scalars(select(Stocktake).order_by(Stocktake.created_at.desc()).limit(50))).all()
    counts = dict(
        (
            await session.execute(
                select(StocktakeItem.stocktake_id, func.count())
                .where(StocktakeItem.count != "extra")
                .group_by(StocktakeItem.stocktake_id)
            )
        ).all()
    )
    run = await latest_run(session)
    return {
        "items": [{**_stocktake_out(s), "book": counts.get(s.id, 0)} for s in rows],
        "last_sync": run.finished_at.isoformat() if run and run.finished_at else None,
    }


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cutoff: date


@router.post("/stocktakes", status_code=201)
async def create_stocktake(
    body: CreateIn, user: CurrentUser = Depends(manage_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Close the books: every IT asset in the latest SDP sync becomes an item to count."""
    run = await latest_run(session)
    if run is None:
        raise AppError("Chưa có lần đồng bộ SDP nào – chạy Đồng bộ dữ liệu trước.", code="no_sync")
    settings = await load_settings(session)
    snaps = (
        await session.scalars(
            select(Snapshot)
            .where(Snapshot.run_id == run.id, Snapshot.system.in_(["sdp_asset", "sdp_user"]))
            .order_by(Snapshot.id)
        )
    ).all()
    users = {x.data.get("id"): x.data for x in snaps if x.system == "sdp_user"}
    assets = [
        x.data for x in snaps if x.system == "sdp_asset" and in_book(x.data, settings.asset_state_disposed)
    ]
    if not assets:
        raise AppError("Lần đồng bộ gần nhất không có tài sản SDP nào.", code="no_assets")
    s = Stocktake(
        name=body.name.strip(),
        cutoff=body.cutoff,
        status="counting",
        source_run_id=run.id,
        source_synced_at=run.finished_at,
        created_by=user.upn,
    )
    session.add(s)
    await session.flush()
    # The areas as they stand now become this stocktake's zones; each asset goes to its user's area.
    areas = (
        await session.scalars(select(StocktakeArea).order_by(StocktakeArea.position, StocktakeArea.name))
    ).all()
    zones = {}
    for n, a in enumerate(areas):
        zones[a.name] = StocktakeZone(stocktake_id=s.id, name=a.name, is_store=a.is_store, position=n)
        session.add(zones[a.name])
    await session.flush()
    rules = [_rule(a) for a in areas]
    seen: set[str] = set()
    for a in assets:
        item = book_item(a, settings.asset_fa_code_field)
        if not item["asset_id"] or item["asset_id"] in seen:
            continue
        seen.add(item["asset_id"])
        owner = _owner(a, users)
        zone = zones.get(area_for(owner, rules) or "")
        session.add(
            StocktakeItem(
                stocktake_id=s.id,
                count="pending",
                employee_id=(owner or {}).get("employee_id"),
                department=(owner or {}).get("department") or None,
                zone_id=zone.id if zone else None,
                **item,
            )
        )
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_created",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=s.name,
        after={"cutoff": body.cutoff.isoformat(), "assets": len(seen)},
    )
    await session.commit()
    return {**_stocktake_out(s), "book": len(seen)}


@router.get("/stocktakes/{stocktake_id}")
async def get_stocktake(
    stocktake_id: uuid.UUID,
    user: CurrentUser = Depends(view_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    s = await _get(session, stocktake_id)
    rv = s.rules_version
    items = [_item_out(i, rv) for i in await _items(session, stocktake_id)]
    zones = await _zones(session, stocktake_id)
    if not user.has("lifecycle.stocktake.view_all"):  # operators see only the zones handed to them
        mine = {str(z) for z in await _my_zone_ids(session, user, stocktake_id)}
        zones = [z for z in zones if z["id"] in mine]
        items = [i for i in items if i["zone_id"] in mine]
    out = {**_stocktake_out(s), "summary": summary(items, rv), "zones": zone_progress(zones, items, rv)}
    out["items"], out["record"] = items, None
    if user.has(
        "lifecycle.stocktake.view_all"
    ):  # the whole-period record and the unzoned warning: managers only
        out["summary"]["unzoned"] = sum(1 for i in items if not i["zone_id"])
        record = await session.get(Document, s.record_document_id) if s.record_document_id else None
        out["record"] = _record_out(record)
    return out


class ScanIn(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    seen_at: str = Field("", max_length=256)
    zone_id: uuid.UUID | None = None  # the zone being walked: a still-pending asset found here moves here


@router.post("/stocktakes/{stocktake_id}/scan")
async def scan(
    stocktake_id: uuid.UUID,
    body: ScanIn,
    user: CurrentUser = Depends(count_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """A serial / asset tag / accounting code read on site. Marks the matching asset as present (idempotent)."""
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    zone = await _walked_zone(session, user, s, body.zone_id)
    hits = await _matching(session, stocktake_id, [body.code])
    if not hits:
        return {"matched": False, "item": None, "already": False}
    if len(hits) > 1:
        names = ", ".join(f"{i.name} ({i.asset_tag or i.serial or '—'})" for i in hits[:5])
        raise Conflict(f"Mã “{body.code.strip()}” khớp {len(hits)} tài sản: {names}. Tick trực tiếp trong danh sách.",
                       code="ambiguous_code")  # fmt: skip
    item = hits[0]
    already = item.count in ("found", "extra")
    if already and not await _can_touch(session, user, s, item):
        return {"matched": True, "item": None, "already": True}  # counted in someone else's zone: no details
    if not already:
        kind_before = diff_kind(_item_out(item, s.rules_version), s.rules_version)
        before: dict[str, Any] = {"count": item.count}
        after: dict[str, Any] = {"count": "found", "code": body.code.strip()}
        if zone and item.count == "pending" and item.zone_id != zone.id:
            # First sighting wins: move it to the zone it was found in and keep where it was booked.
            names = {z["id"]: z["name"] for z in await _zones(session, s.id)}
            origin = names.get(str(item.zone_id)) if item.zone_id else None
            before["zone"], after["zone"] = origin, zone.name
            item.zone_id = zone.id
            item.seen_at = origin or "Chưa phân khu"
        else:
            item.seen_at = body.seen_at.strip() or item.seen_at
        item.count, item.condition = "found", item.condition or "good"
        item.counted_by, item.counted_at = user.upn, _now()
        _recheck_resolution(item, kind_before, s.rules_version)
        await record(
            session,
            actor=user.actor,
            module_id=MODULE_ID,
            action="lifecycle.stocktake_counted",
            target_type="stocktake",
            target_id=str(s.id),
            target_label=f"{s.name} · {item.name} ({item.asset_tag or item.serial or item.asset_id})",
            before=before,
            after=after,
        )
        await session.commit()
    return {"matched": True, "item": _item_out(item, s.rules_version), "already": already}


class CountIn(BaseModel):
    count: Literal["pending", "found", "missing"]
    condition: Condition | None = None
    seen_at: str = Field("", max_length=256)
    note: str = Field("", max_length=1000)


@router.put("/stocktakes/{stocktake_id}/items/{item_id}")
async def set_count(
    stocktake_id: uuid.UUID,
    item_id: int,
    body: CountIn,
    user: CurrentUser = Depends(count_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    item = await session.get(StocktakeItem, item_id)
    if item is None or item.stocktake_id != s.id:
        raise NotFound("Không tìm thấy tài sản trong kỳ kiểm kê.")
    if not await _can_touch(session, user, s, item):
        raise PermissionDenied("Chỉ kiểm được tài sản thuộc khu bạn được giao.")
    if item.count == "extra":
        raise Conflict("Tài sản ngoài sổ: sửa ghi chú hoặc xoá dòng rồi thêm lại.", code="extra_item")
    if s.status == "reviewing" and body.count == "pending":
        raise Conflict("Đã kết thúc kiểm: chọn Có hoặc Không tìm thấy.", code="count_finished")
    before = {"count": item.count, "condition": item.condition, "seen_at": item.seen_at, "note": item.note}
    kind_before = diff_kind(_item_out(item, s.rules_version), s.rules_version)
    item.count = body.count
    item.condition = (body.condition or "good") if body.count == "found" else None
    item.seen_at, item.note = body.seen_at.strip(), body.note.strip()
    item.counted_by, item.counted_at = (user.upn, _now()) if body.count != "pending" else (None, None)
    _recheck_resolution(item, kind_before, s.rules_version)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_counted",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · {item.name} ({item.asset_tag or item.serial or item.asset_id})",
        before=before,
        after={"count": item.count, "condition": item.condition, "seen_at": item.seen_at, "note": item.note},
    )
    await session.commit()
    return _item_out(item, s.rules_version)


class CountBatchIn(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=100)
    count: Literal["found", "missing"]
    condition: Condition | None = None


@router.post("/stocktakes/{stocktake_id}/count-batch")
async def count_batch(
    stocktake_id: uuid.UUID,
    body: CountBatchIn,
    user: CurrentUser = Depends(count_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Count many assets at once (a whole desk). All or nothing: one bad id refuses the lot. Only pending
    assets change; the ones already counted are skipped (and reported in ``skipped``)."""
    s = await _get(session, stocktake_id, lock=True)
    if s.status != "counting":
        raise Conflict("Kỳ kiểm kê không ở bước kiểm.", code="not_counting")
    ids = list(dict.fromkeys(body.item_ids))
    items = {
        i.id: i
        for i in (
            await session.scalars(
                select(StocktakeItem).where(StocktakeItem.stocktake_id == s.id, StocktakeItem.id.in_(ids))
            )
        ).all()
    }
    if len(items) != len(ids):
        raise NotFound("Có tài sản không thuộc kỳ kiểm kê.")
    if any(i.count == "extra" for i in items.values()):
        raise Conflict("Tài sản ngoài sổ không kiểm hàng loạt được.", code="extra_item")
    if not user.has("lifecycle.stocktake.view_all"):
        mine = await _my_zone_ids(session, user, s.id)
        if any(i.zone_id not in mine for i in items.values()):
            raise PermissionDenied("Chỉ kiểm được tài sản thuộc khu bạn được giao.")
    skipped = 0
    for iid in ids:
        item = items[iid]
        if item.count != "pending":  # already counted (maybe by someone else): never overwrite it
            skipped += 1
            continue
        kind_before = diff_kind(_item_out(item, s.rules_version), s.rules_version)
        before = {"count": item.count, "condition": item.condition}
        item.count = body.count
        item.condition = (body.condition or "good") if body.count == "found" else None
        item.counted_by, item.counted_at = user.upn, _now()
        _recheck_resolution(item, kind_before, s.rules_version)
        await record(
            session,
            actor=user.actor,
            module_id=MODULE_ID,
            action="lifecycle.stocktake_counted",
            target_type="stocktake",
            target_id=str(s.id),
            target_label=f"{s.name} · {item.name} ({item.asset_tag or item.serial or item.asset_id})",
            before=before,
            after={"count": item.count, "condition": item.condition, "batch": len(ids)},
        )
    await session.commit()
    return {"items": [_item_out(items[i], s.rules_version) for i in ids], "skipped": skipped}


class ExtraIn(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    product_type: str = Field("", max_length=128)
    serial: str = Field("", max_length=128)
    asset_tag: str = Field("", max_length=128)
    fa_code: str = Field("", max_length=128)
    condition: Condition = "good"
    seen_at: str = Field("", max_length=256)
    note: str = Field("", max_length=1000)
    zone_id: uuid.UUID | None = None  # required without view_all: the zone being walked


@router.post("/stocktakes/{stocktake_id}/extras", status_code=201)
async def add_extra(
    stocktake_id: uuid.UUID,
    body: ExtraIn,
    user: CurrentUser = Depends(count_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """An asset found on site that is not on the SDP book."""
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    zone = await _walked_zone(session, user, s, body.zone_id)
    if not (body.serial.strip() or body.asset_tag.strip() or body.fa_code.strip()):
        raise AppError(
            "Nhập ít nhất serial, asset tag hoặc mã TSCĐ để nhận diện tài sản.", code="extra_no_code"
        )
    dups = await _matching(
        session, stocktake_id, [c for c in (body.serial, body.asset_tag, body.fa_code) if c.strip()]
    )
    if dups:
        i = dups[0]
        detail = (
            f": {i.name} ({i.asset_tag or i.serial or '—'})" if await _can_touch(session, user, s, i) else ""
        )
        raise Conflict(f"Mã đã có trong kỳ kiểm kê{detail}.", code="extra_duplicate")
    item = StocktakeItem(
        stocktake_id=s.id,
        asset_id=None,
        name=body.name.strip(),
        product_type=body.product_type.strip() or None,
        serial=body.serial.strip() or None,
        asset_tag=body.asset_tag.strip() or None,
        fa_code=body.fa_code.strip() or None,
        zone_id=zone.id if zone else None,
        count="extra",
        condition=body.condition,
        seen_at=body.seen_at.strip(),
        note=body.note.strip(),
        counted_by=user.upn,
        counted_at=_now(),
    )
    session.add(item)
    await session.flush()
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_extra_added",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · {item.name}",
        after={"serial": item.serial, "asset_tag": item.asset_tag, "fa_code": item.fa_code},
    )
    await session.commit()
    return _item_out(item, s.rules_version)


@router.delete("/stocktakes/{stocktake_id}/extras/{item_id}", status_code=204)
async def delete_extra(
    stocktake_id: uuid.UUID,
    item_id: int,
    user: CurrentUser = Depends(count_perm),
    session: AsyncSession = Depends(get_session),
) -> Response:
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    item = await session.get(StocktakeItem, item_id)
    if item is None or item.stocktake_id != s.id or item.count != "extra":
        raise NotFound("Không tìm thấy tài sản ngoài sổ.")
    if not await _can_touch(session, user, s, item):
        raise PermissionDenied("Chỉ xoá được tài sản thuộc khu bạn được giao.")
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_extra_removed",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · {item.name}",
        before={"serial": item.serial, "asset_tag": item.asset_tag, "fa_code": item.fa_code},
    )
    await session.delete(item)
    await session.commit()
    return Response(status_code=204)


class ResolveIn(BaseModel):
    resolution: Resolution | None
    note: str = Field("", max_length=1000)


@router.put("/stocktakes/{stocktake_id}/items/{item_id}/resolution")
async def resolve(
    stocktake_id: uuid.UUID,
    item_id: int,
    body: ResolveIn,
    user: CurrentUser = Depends(manage_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """How a difference is handled (agreed with Hành chánh / Kế toán)."""
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    item = await session.get(StocktakeItem, item_id)
    if item is None or item.stocktake_id != s.id:
        raise NotFound("Không tìm thấy tài sản trong kỳ kiểm kê.")
    kind = diff_kind(_item_out(item, s.rules_version), s.rules_version)
    if not kind:
        raise Conflict("Tài sản này khớp sổ, không cần hướng xử lý.", code="no_difference")
    if body.resolution and body.resolution not in ALLOWED_RESOLUTIONS.get(kind, ()):
        raise AppError(
            "Hướng xử lý này không áp dụng cho loại chênh lệch của tài sản.", code="resolution_not_allowed"
        )
    if body.resolution == "investigate" and not body.note.strip():
        raise AppError("Ghi rõ cần tìm hoặc làm rõ điều gì.", code="resolution_note_required")
    before = {"resolution": item.resolution, "note": item.resolution_note}
    item.resolution, item.resolution_note = body.resolution, body.note.strip()
    item.resolved_by, item.resolved_at = (user.upn, _now()) if body.resolution else (None, None)
    item.resolution_suggested = False  # a person decided
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_resolved",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · {item.name} ({item.asset_tag or item.serial or '—'})",
        before=before,
        after={"resolution": body.resolution, "note": item.resolution_note},
    )
    await session.commit()
    return _item_out(item, s.rules_version)


@router.post("/stocktakes/{stocktake_id}/default-resolutions")
async def default_resolutions(
    stocktake_id: uuid.UUID,
    user: CurrentUser = Depends(manage_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Suggest a resolution for every difference that has none. They stay unresolved (and block closing)
    until a person confirms each one."""
    s = await _get(session, stocktake_id, lock=True)
    if s.status != "reviewing":
        raise Conflict("Chỉ áp hướng xử lý mặc định khi đang xử lý chênh lệch.", code="not_reviewing")
    counts: dict[str, int] = {}
    for i in await _items(session, stocktake_id):
        kind = diff_kind(_item_out(i, s.rules_version), s.rules_version)
        if not kind or i.resolution or kind not in DEFAULT_RESOLUTIONS:
            continue
        i.resolution, i.resolution_note = DEFAULT_RESOLUTIONS[kind]
        i.resolved_by, i.resolved_at, i.resolution_suggested = None, None, True
        counts[kind] = counts.get(kind, 0) + 1
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_default_resolutions",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=s.name,
        after={"suggested": sum(counts.values()), **counts},
    )
    await session.commit()
    return {"suggested": sum(counts.values()), **counts}


@router.post("/stocktakes/{stocktake_id}/finish-count")
async def finish_count(
    stocktake_id: uuid.UUID,
    user: CurrentUser = Depends(manage_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """End the site count: every asset not seen becomes "Không tìm thấy" and the differences are handled next."""
    s = await _get(session, stocktake_id, lock=True)
    if s.status != "counting":
        raise Conflict("Kỳ kiểm kê không ở bước kiểm.", code="not_counting")
    missing = 0
    for i in await _items(session, stocktake_id):
        if i.count == "pending":
            i.count, missing = "missing", missing + 1
    s.status = "reviewing"
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_count_finished",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=s.name,
        before={"status": "counting"},
        after={"status": "reviewing", "missing": missing},
    )
    await session.commit()
    return _stocktake_out(s)


@router.post("/stocktakes/{stocktake_id}/close")
async def close_stocktake(
    stocktake_id: uuid.UUID,
    user: CurrentUser = Depends(manage_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    s = await _get(session, stocktake_id, lock=True)
    if s.status != "reviewing":
        raise Conflict("Kết thúc kiểm trước khi đóng kỳ.", code="not_reviewing")
    items = [_item_out(i, s.rules_version) for i in await _items(session, stocktake_id)]
    if left := summary(items, s.rules_version)["unresolved"]:
        raise Conflict(f"Còn {left} chênh lệch chưa có hướng xử lý.", code="differences_unresolved")
    s.status, s.closed_by, s.closed_at = "closed", user.upn, _now()
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_closed",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=s.name,
        before={"status": "reviewing"},
        after={"status": "closed", **summary(items, s.rules_version)},
    )
    await session.commit()
    return _stocktake_out(s)


@router.get("/stocktakes/{stocktake_id}/export")
async def export_stocktake(
    stocktake_id: uuid.UUID,
    user: CurrentUser = Depends(view_perm),
    session: AsyncSession = Depends(get_session),
    zone_id: str | None = None,
) -> Response:
    """The whole stocktake (file tổng: every zone together), or one zone's part (file thành phần, ?zone_id=;
    "none" = assets in no zone). Without lifecycle.stocktake.view_all only the zones handed to me."""
    s = await _get(session, stocktake_id)
    zones = await _zones(session, stocktake_id)
    if not user.has("lifecycle.stocktake.view_all") and not any(
        z["id"] == zone_id and z["assignee_user_id"] == str(user.id) for z in zones
    ):
        raise PermissionDenied("Chỉ tải được file của khu bạn được giao kiểm.")
    names = {z["id"]: z["name"] for z in zones}
    items = [
        {**(o := _item_out(i, s.rules_version)), "zone": names.get(o["zone_id"] or "")}
        for i in await _items(session, stocktake_id)
    ]
    part = ""
    if zone_id:
        if zone_id != "none" and zone_id not in names:
            raise NotFound("Không tìm thấy khu trong kỳ kiểm kê.")
        items = [i for i in items if (i["zone_id"] or "none") == zone_id]
        zones = [z for z in zones if z["id"] == zone_id]
        part = names.get(zone_id, "Chưa phân khu")
    # Money sheets: the full file only, and only for someone with the finance permission.
    recon = (
        await _reconcile(session, s, zones)
        if not zone_id
        and user.has("lifecycle.stocktake.finance")
        and user.has("lifecycle.stocktake.view_all")
        and s.fa_registers
        else None
    )
    head = {
        **_stocktake_out(s),
        "cutoff": fmt_date(s.cutoff),
        "source_synced_at": fmt_date(s.source_synced_at) if s.source_synced_at else "",
    }
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_exported",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · {part}" if part else s.name,
        after={"finance_sheets": recon is not None},
    )
    await session.commit()
    if part:
        head["name"] = f"{s.name} – {part}"
    return Response(
        to_xlsx(head, items, zones, await logo_bytes(session), s.rules_version, recon),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="kiem-ke-{s.cutoff:%Y%m%d}{"-" + _slug(part) if part else ""}.xlsx"'
        },
    )


def _slug(text: str) -> str:
    """ASCII file-name part ("Tầng 5" → "tang-5")."""
    import unicodedata

    plain = (
        unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
        .encode("ascii", "ignore")
        .decode()
    )
    return "-".join("".join(c if c.isalnum() else " " for c in plain.lower()).split())[:40] or "khu"


# ---------------------------------------------------------------- biên bản kiểm kê (IT + Kế toán)


def _record_out(d: Document | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {
        "id": str(d.id),
        "status": d.status,
        "number": d.number,
        "has_pdf": d.pdf_file_id is not None,
        "has_signed": d.signed_file_id is not None,
        "data": {
            k: d.data.get(k) for k in ("it_rep", "it_title", "finance_rep", "finance_title", "conclusion")
        },
        "version": d.version,
    }


class RecordIn(BaseModel):
    it_rep: str = Field(min_length=1, max_length=200)
    it_title: str = Field("", max_length=200)
    finance_rep: str = Field(min_length=1, max_length=200)
    finance_title: str = Field("", max_length=200)
    conclusion: str = Field("", max_length=2000)


def _record_data(
    s: Stocktake,
    zones: list[dict[str, Any]],
    items: list[dict[str, Any]],
    body: RecordIn,
    book_counts: dict[str, int],
) -> dict[str, Any]:
    names = {z["id"]: z["name"] for z in zones}
    rv = s.rules_version
    totals = summary(items, rv)
    found = [i for i in items if i["count"] == "found"]
    light = sum(1 for i in found if i.get("condition") == "damaged")
    broken = sum(1 for i in found if i.get("condition") == "broken")
    diffs = []
    for i in items:
        if not (d := difference(i, rv)):
            continue
        res = RESOLUTION_LABEL.get(i.get("resolution") or "", "")
        diffs.append(
            {
                "name": i["name"],
                "codes": " / ".join(c for c in (i.get("asset_tag"), i.get("serial")) if c),
                "fa_code": i.get("fa_code") or "",
                "where": " · ".join(w for w in (i.get("user_name"), names.get(i.get("zone_id") or "")) if w),
                "difference": d,
                "resolution": f"{res} – {i['resolution_note']}" if i.get("resolution_note") else res,
            }
        )
    return {
        "stocktake_id": str(s.id),
        "title": s.name,
        "cutoff": s.cutoff.isoformat(),
        "date": today_vn().isoformat(),
        "synced_at": fmt_date(s.source_synced_at) if s.source_synced_at else "",
        "totals": {
            "book": totals["book"],
            "found": totals["found"],
            "missing": totals["missing"],
            "extra": totals["extra"],
            "damaged": broken + light if rv < 2 else broken,
            "damaged_light": light if rv >= 2 else 0,  # rules 1 already counts light damage in "damaged"
            "differences": totals["differences"],
            **book_counts,  # accounting register: counts only, never money
        },
        "zones": [
            {
                "name": z["name"],
                "assignee": z.get("assignee_name") or "",
                "book": z["book"],
                "found": z["found"],
                "missing": z["missing"],
                "extra": z["extra"],
            }
            for z in zone_progress(zones, items, rv)
        ],
        "differences": diffs,
        "assets": [
            {
                "zone": names.get(i.get("zone_id") or "", "Chưa phân khu"),
                "name": i["name"],
                "type": i.get("product_type") or "",
                "codes": " / ".join(c for c in (i.get("asset_tag"), i.get("serial")) if c),
                "fa_code": i.get("fa_code") or "",
                "user": f"{i['user_name']} ({i['employee_id']})"
                if i.get("user_name") and i.get("employee_id")
                else i.get("user_name") or "",
                "result": COUNT_LABEL[i["count"]],
                "condition": CONDITION_LABEL.get(i.get("condition") or "", ""),
            }
            for i in sorted(
                items,
                key=lambda x: (names.get(x.get("zone_id") or "", "~"), x.get("user_name") or "", x["id"]),
            )
        ],
        **body.model_dump(),
    }


@router.post("/stocktakes/{stocktake_id}/record")
async def make_record(
    stocktake_id: uuid.UUID,
    body: RecordIn,
    user: CurrentUser = Depends(manage_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Draft (or refresh the draft of) the stocktake record from the closed stocktake. It is then numbered
    (Chốt biên bản), printed, signed by IT and Kế toán, and the signed scan uploaded (core document endpoints)."""
    from app.modules.lifecycle.stocktake_record import STOCKTAKE_RECORD

    s = await _get(session, stocktake_id, lock=True)
    if s.status != "closed":
        raise Conflict(
            "Đóng kỳ kiểm kê (mọi chênh lệch đã có hướng xử lý) trước khi lập biên bản.", code="not_closed"
        )
    items = [_item_out(i, s.rules_version) for i in await _items(session, stocktake_id)]
    zones = await _zones(session, stocktake_id)
    data = _record_data(s, zones, items, body, register_totals(await _reconcile(session, s, zones)))
    doc = await session.get(Document, s.record_document_id) if s.record_document_id else None
    if doc is not None and doc.status == "void":
        doc = None
    if doc is None:
        doc = await documents.create_draft(session, user.actor, STOCKTAKE_RECORD, MODULE_ID, data)
        s.record_document_id = doc.id
    elif doc.status == "draft":
        await documents.update_draft(session, user.actor, doc, STOCKTAKE_RECORD, data, doc.version)
    else:
        raise Conflict("Biên bản đã chốt số – huỷ biên bản trước nếu cần lập lại.", code="record_finalized")
    await session.commit()
    return _record_out(doc)  # type: ignore[return-value]


@router.get("/stocktake-meta")
async def stocktake_meta(_: CurrentUser = Depends(view_perm)) -> dict[str, Any]:
    return {"resolutions": RESOLUTION_LABEL, "allowed": ALLOWED_RESOLUTIONS}


# ---------------------------------------------------------------- zones of one stocktake: who counts what


async def _counters(session: AsyncSession) -> list[dict[str, Any]]:
    """Active people who may count (lifecycle.stocktake.count)."""
    from app.core.auth.models import User
    from app.core.modules.registry import get_registry
    from app.core.rbac.service import permissions_for_user

    users = (
        await session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name))
    ).all()
    return [
        {"id": str(u.id), "name": u.display_name, "upn": u.upn}
        for u in users
        if "lifecycle.stocktake.count" in await permissions_for_user(session, u.id, get_registry())
    ]


@router.get("/stocktake-counters")
async def stocktake_counters(
    _: CurrentUser = Depends(assign_perm), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    return await _counters(session)


class ZoneAssignIn(BaseModel):
    user_id: uuid.UUID | None


@router.put("/stocktakes/{stocktake_id}/zones/{zone_id}")
async def assign_zone(
    stocktake_id: uuid.UUID,
    zone_id: uuid.UUID,
    body: ZoneAssignIn,
    user: CurrentUser = Depends(assign_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Hand a zone to the person who counts it (None = nobody yet). Without view_all that person counts only
    the assets of their zones; a device found on another floor moves to the zone it is scanned in."""
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    zone = await session.get(StocktakeZone, zone_id)
    if zone is None or zone.stocktake_id != s.id:
        raise NotFound("Không tìm thấy khu trong kỳ kiểm kê.")
    name = None
    if body.user_id:
        name = next((c["name"] for c in await _counters(session) if c["id"] == str(body.user_id)), None)
        if name is None:
            raise AppError("Người này không có quyền kiểm kê.", code="not_a_counter")
    before = {"assignee": zone.assignee_name}
    zone.assignee_user_id, zone.assignee_name = body.user_id, name
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_zone_assigned",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · {zone.name}",
        before=before,
        after={"assignee": name},
    )
    await session.commit()
    return _zone_out(zone)


class ItemZoneIn(BaseModel):
    zone_id: uuid.UUID | None


@router.put("/stocktakes/{stocktake_id}/items/{item_id}/zone")
async def move_item(
    stocktake_id: uuid.UUID,
    item_id: int,
    body: ItemZoneIn,
    user: CurrentUser = Depends(assign_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Put an asset in another zone (e.g. one that was in no area, or whose user moved floor)."""
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    item = await session.get(StocktakeItem, item_id)
    if item is None or item.stocktake_id != s.id:
        raise NotFound("Không tìm thấy tài sản trong kỳ kiểm kê.")
    zone = await session.get(StocktakeZone, body.zone_id) if body.zone_id else None
    if body.zone_id and (zone is None or zone.stocktake_id != s.id):
        raise NotFound("Không tìm thấy khu trong kỳ kiểm kê.")
    names = {z["id"]: z["name"] for z in await _zones(session, s.id)}
    before = {"zone": names.get(str(item.zone_id)) if item.zone_id else None}
    item.zone_id = zone.id if zone else None
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_item_moved",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · {item.name} ({item.asset_tag or item.serial or '—'})",
        before=before,
        after={"zone": zone.name if zone else None},
    )
    await session.commit()
    return _item_out(item, s.rules_version)


# ---------------------------------------------------------------- areas (khu kiểm kê), kept from year to year


def _area_out(a: StocktakeArea) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "name": a.name,
        "is_store": a.is_store,
        "departments": a.departments,
        "employee_ids": a.employee_ids,
        "position": a.position,
    }


@router.get("/stocktake-areas")
async def list_areas(
    _: CurrentUser = Depends(view_all_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """The areas, the departments of the latest SDP sync, and the people holding assets who are in no area."""
    areas = (
        await session.scalars(select(StocktakeArea).order_by(StocktakeArea.position, StocktakeArea.name))
    ).all()
    rules = [_rule(a) for a in areas]
    run = await latest_run(session)
    departments: dict[str, int] = {}
    uncovered: dict[str, dict[str, Any]] = {}
    per_area: dict[str, int] = {a.name: 0 for a in areas}
    unused_without_store = 0
    if run:
        snaps = (
            await session.scalars(
                select(Snapshot).where(
                    Snapshot.run_id == run.id, Snapshot.system.in_(["sdp_asset", "sdp_user"])
                )
            )
        ).all()
        users = {x.data.get("id"): x.data for x in snaps if x.system == "sdp_user"}
        for x in snaps:
            if x.system != "sdp_asset":
                continue
            owner = _owner(x.data, users)
            if owner and (d := owner.get("department")):
                departments[d] = departments.get(d, 0) + 1
            if (hit := area_for(owner, rules)) is not None:
                per_area[hit] += 1
            elif owner:
                key = owner.get("employee_id") or owner.get("name") or ""
                u = uncovered.setdefault(
                    key,
                    {"employee_id": owner.get("employee_id"), "name": owner.get("name") or "",
                     "department": owner.get("department"), "assets": 0},
                )  # fmt: skip
                u["assets"] += 1
            else:
                unused_without_store += 1
    return {
        "areas": [{**_area_out(a), "assets": per_area.get(a.name, 0)} for a in areas],
        "departments": [{"name": k, "assets": v} for k, v in sorted(departments.items())],
        "uncovered": sorted(uncovered.values(), key=lambda u: (u["department"] or "", u["name"]))[:300],
        "uncovered_total": len(uncovered),
        "unused_without_store": unused_without_store,
        "last_sync": run.finished_at.isoformat() if run and run.finished_at else None,
    }


class AreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_store: bool = False
    departments: list[str] = Field(default_factory=list, max_length=200)
    employee_ids: list[str] = Field(default_factory=list, max_length=2000)
    position: int = Field(0, ge=0, le=1000)


async def _check_area(
    session: AsyncSession, body: AreaIn, area_id: uuid.UUID | None
) -> tuple[list[str], list[str]]:
    """Normalized departments / IDs; a department, an employee ID or the store role belongs to one area only."""
    depts = list(dict.fromkeys(d.strip() for d in body.departments if d.strip()))
    ids = list(dict.fromkeys(_norm_id(e) for e in body.employee_ids if e.strip()))
    if bad := [e for e in ids if not HEADER_ID.match(e)]:
        raise AppError(f"Mã NV không hợp lệ: {', '.join(bad[:5])}.", code="bad_employee_ids")
    others = [a for a in (await session.scalars(select(StocktakeArea))).all() if a.id != area_id]
    if any(a.name.lower() == body.name.strip().lower() for a in others):
        raise Conflict("Đã có khu cùng tên.", code="area_name_taken")
    if body.is_store and (store := next((a for a in others if a.is_store), None)):
        raise Conflict(
            f"Khu “{store.name}” đã là khu kho (tài sản không có người dùng).", code="store_area_taken"
        )
    for a in others:
        if both := {d.lower() for d in depts} & {d.lower() for d in a.departments}:
            raise Conflict(
                f"Phòng ban đã thuộc khu “{a.name}”: {', '.join(sorted(both)[:3])}.", code="department_taken"
            )
        if both := set(ids) & set(a.employee_ids):
            raise Conflict(
                f"Mã NV đã thuộc khu “{a.name}”: {', '.join(sorted(both)[:5])}.", code="employee_taken"
            )
    return depts, ids


@router.post("/stocktake-areas", status_code=201)
async def create_area(
    body: AreaIn, user: CurrentUser = Depends(manage_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    depts, ids = await _check_area(session, body, None)
    a = StocktakeArea(name=body.name.strip(), is_store=body.is_store, departments=depts, employee_ids=ids,
                      position=body.position, updated_by=user.upn)  # fmt: skip
    session.add(a)
    await session.flush()
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_area_created",
        target_type="stocktake_area",
        target_id=str(a.id),
        target_label=a.name,
        after={"departments": depts, "employee_ids": len(ids), "is_store": a.is_store},
    )
    await session.commit()
    return _area_out(a)


@router.put("/stocktake-areas/{area_id}")
async def update_area(
    area_id: uuid.UUID,
    body: AreaIn,
    user: CurrentUser = Depends(manage_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    a = await session.get(StocktakeArea, area_id, with_for_update=True)
    if a is None:
        raise NotFound("Không tìm thấy khu kiểm kê.")
    depts, ids = await _check_area(session, body, a.id)
    before = _area_out(a)
    a.name, a.is_store, a.departments, a.employee_ids = body.name.strip(), body.is_store, depts, ids
    a.position, a.updated_by, a.updated_at = body.position, user.upn, _now()
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_area_updated",
        target_type="stocktake_area",
        target_id=str(a.id),
        target_label=a.name,
        before=before,
        after=_area_out(a),
    )
    await session.commit()
    return _area_out(a)


@router.delete("/stocktake-areas/{area_id}", status_code=204)
async def delete_area(
    area_id: uuid.UUID, user: CurrentUser = Depends(manage_perm), session: AsyncSession = Depends(get_session)
) -> Response:
    """Stocktakes already opened keep their zones."""
    a = await session.get(StocktakeArea, area_id)
    if a is None:
        raise NotFound("Không tìm thấy khu kiểm kê.")
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_area_deleted",
        target_type="stocktake_area",
        target_id=str(a.id),
        target_label=a.name,
        before=_area_out(a),
    )
    await session.delete(a)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- "Việc tiếp theo": the zones handed to me


async def next_action(session: AsyncSession, user: Any) -> list[dict[str, Any]]:
    """One item per open stocktake where I count zones that still have assets to check."""
    zones = (
        await session.execute(
            select(StocktakeZone, Stocktake)
            .join(Stocktake, Stocktake.id == StocktakeZone.stocktake_id)
            .where(StocktakeZone.assignee_user_id == user.id, Stocktake.status == "counting")
            .order_by(StocktakeZone.position)
        )
    ).all()
    items: list[dict[str, Any]] = []
    for st in {s for _, s in zones}:
        mine = [z for z, s in zones if s.id == st.id]
        left = dict(
            (
                await session.execute(
                    select(StocktakeItem.zone_id, func.count())
                    .where(StocktakeItem.zone_id.in_([z.id for z in mine]), StocktakeItem.count == "pending")
                    .group_by(StocktakeItem.zone_id)
                )
            ).all()
        )
        if not (total := sum(left.values())):
            continue
        names = ", ".join(z.name for z in mine if left.get(z.id))
        items.append(
            {
                "key": f"lifecycle.stocktake.{st.id}",
                "kind": "stocktake",
                "priority": 2,
                "title": f"Kiểm kê {names} – còn {total} tài sản",
                "detail": st.name,
                "due": st.cutoff.isoformat(),
                "href": f"/lifecycle/stocktake/{st.id}",
                "action": "Đi kiểm",
            }
        )
    return items


# --- Accounting registers (sổ TSCĐ / CCDC) -------------------------------------------------------------


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise NotFound("Loại sổ không hợp lệ (tscd hoặc ccdc).")
    return kind


def _mapping(raw: str | None) -> dict[str, str] | None:
    """Columns picked by hand: a JSON object {field: column letter}."""
    if not raw:
        return None
    try:
        m = json.loads(raw)
    except ValueError:
        m = None
    if not isinstance(m, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in m.items()):
        raise AppError("Cột chọn tay không hợp lệ.", code="register_mapping_invalid")
    return m


async def _parse_upload(file: UploadFile, sheet: str | None, mapping: str | None) -> tuple[bytes, Parsed]:
    data = await file.read(MAX_FILE_BYTES + 1)
    check_size(data)
    sniff(data, {XLSX_MIME})
    return data, await asyncio.to_thread(parse_register, data, sheet or None, _mapping(mapping))


def _token(digest: str, parsed: Parsed, mapping: str | None) -> str:
    """Binds an import to the exact file + sheet + column choice that was previewed."""
    canon = json.dumps(_mapping(mapping) or {}, sort_keys=True)
    return hashlib.sha256(f"{digest}|{parsed.sheet}|{canon}".encode()).hexdigest()


def _row_out(r: Any) -> dict[str, Any]:
    return {
        "row_no": r.row_no,
        "code": r.code,
        "name": r.name,
        "group": r.group,
        "quantity": str(r.quantity) if r.quantity is not None else None,
        "cost": str(r.cost) if r.cost is not None else None,
        "accumulated": str(r.accumulated) if r.accumulated is not None else None,
        "net": str(r.net) if r.net is not None else None,
        "in_use_date": r.in_use_date.isoformat() if r.in_use_date else None,
        "department": r.department,
        "life_months": r.life_months,
        "account": r.account,
        "serial": r.serial,
    }


@router.get("/stocktakes/{stocktake_id}/reconciliation")
async def reconciliation(
    stocktake_id: uuid.UUID,
    _: CurrentUser = Depends(finance_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Sổ KT ↔ SDP ↔ thực tế, per code (finance only: carries nguyên giá / giá trị còn lại)."""
    s = await _get(session, stocktake_id)
    recon = await _reconcile(session, s, await _zones(session, stocktake_id))
    return jsonable(
        {
            **recon,
            "totals": register_totals(recon),
            "meta": {
                "registers": s.fa_registers,
                "rules_version": s.rules_version,
                "provisional": s.status
                == "counting",  # still being counted: pending lines, figures may change
            },
        }
    )


@router.post("/stocktakes/{stocktake_id}/registers/{kind}/preview")
async def preview_register(
    stocktake_id: uuid.UUID,
    kind: str,
    file: UploadFile = File(...),
    sheet: str | None = Form(None),
    mapping: str | None = Form(None),
    user: CurrentUser = Depends(finance_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Step 1 of the import: how the columns were read, the first rows and the total. Nothing is stored.
    The import must echo ``preview_token`` back so it applies exactly the file, sheet and columns previewed."""
    _kind(kind)
    await _get(session, stocktake_id)
    data, parsed = await _parse_upload(file, sheet, mapping)
    return {
        "kind": kind,
        "sha256": hashlib.sha256(data).hexdigest(),
        "preview_token": _token(hashlib.sha256(data).hexdigest(), parsed, mapping),
        "headers": parsed.headers,
        "mapping": parsed.positions,
        "ambiguous": parsed.ambiguous,
        "errors": parsed.errors[:100],
        "errors_total": len(parsed.errors),
        "skipped": parsed.skipped,
        "sheet": parsed.sheet,
        "sheets": parsed.sheets,
        "header_rows": parsed.header_rows,
        "columns": parsed.columns,
        "rows": len(parsed.rows),
        "total_cost": str(parsed.total_cost),
        "first_rows": [_row_out(r) for r in parsed.rows[:5]],
        "warnings": parsed.warnings[:50],
        "warnings_total": len(parsed.warnings),
    }


@router.post("/stocktakes/{stocktake_id}/registers/{kind}")
async def import_register(
    stocktake_id: uuid.UUID,
    kind: str,
    file: UploadFile = File(...),
    confirm: bool = Form(False),
    sheet: str | None = Form(None),
    mapping: str | None = Form(None),
    preview_token: str = Form(...),
    as_of: date | None = Form(None),
    user: CurrentUser = Depends(finance_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Step 2: replace the whole register of this kind in one transaction (an import is never merged).
    The file itself is not kept: only its name and sha256 (``preview_token`` must equal the previewed one)."""
    _kind(kind)
    if not confirm:
        raise AppError("Cần xác nhận sau khi xem trước.", code="confirm_required")
    data, parsed = await _parse_upload(file, sheet, mapping)  # parsed before the lock: it can take seconds
    digest = hashlib.sha256(data).hexdigest()
    if _token(digest, parsed, mapping) != preview_token.lower():
        raise Conflict(
            "File, sheet hoặc cột chọn khác với lần xem trước – xem trước lại.", code="register_file_changed"
        )
    if parsed.ambiguous:
        raise AppError(
            "Có cột trùng nhãn (vd. đầu kỳ/cuối kỳ) – chọn cột đúng rồi xem trước lại.",
            code="register_ambiguous_columns",
            details={"ambiguous": parsed.ambiguous},
        )
    if parsed.errors:
        raise AppError(
            f"Còn {len(parsed.errors)} ô không đọc được – sửa file hoặc chọn lại cột rồi xem trước lại.",
            code="register_bad_cells",
            details={"errors": parsed.errors[:100], "errors_total": len(parsed.errors)},
        )
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    await session.execute(
        delete(FaRegisterRow).where(FaRegisterRow.stocktake_id == s.id, FaRegisterRow.kind == kind)
    )
    session.add_all(
        FaRegisterRow(stocktake_id=s.id, kind=kind, **_row_out_orm(r)) for r in parsed.rows
    )  # fmt: skip
    meta = {
        "filename": file.filename or "so-ke-toan.xlsx",
        "sha256": digest,
        "rows": len(parsed.rows),
        "total_cost": str(parsed.total_cost),
        "as_of": as_of.isoformat() if as_of else None,
        "imported_by": user.upn,
        "imported_at": _now().isoformat(),
    }
    before = s.fa_registers.get(kind)
    s.fa_registers = {**s.fa_registers, kind: meta}
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_register_imported",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · sổ {kind.upper()}",
        before={k: before[k] for k in ("sha256", "rows", "total_cost")} if before else None,
        after={k: meta[k] for k in ("filename", "sha256", "rows", "total_cost")},
    )
    await session.commit()
    return {"kind": kind, **meta, "warnings": parsed.warnings[:50], "warnings_total": len(parsed.warnings)}


def _row_out_orm(r: Any) -> dict[str, Any]:
    return {
        k: getattr(r, k)
        for k in ("row_no", "code", "code_norm", "name", "group", "quantity", "cost", "accumulated", "net",
                  "in_use_date", "department", "life_months", "account", "serial")
    }  # fmt: skip


@router.get("/stocktakes/{stocktake_id}/registers")
async def get_registers(
    stocktake_id: uuid.UUID,
    kind: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    _: CurrentUser = Depends(finance_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    s = await _get(session, stocktake_id)
    q = select(FaRegisterRow).where(FaRegisterRow.stocktake_id == s.id)
    if kind:
        q = q.where(FaRegisterRow.kind == _kind(kind))
    total = await session.scalar(select(func.count()).select_from(q.subquery()))
    rows = (
        await session.scalars(
            q.order_by(FaRegisterRow.kind, FaRegisterRow.row_no).offset(offset).limit(limit)
        )
    ).all()
    return {
        "meta": s.fa_registers,
        "total": total,
        "rows": [{"kind": r.kind, **_row_out(r)} for r in rows],
    }


@router.delete("/stocktakes/{stocktake_id}/registers/{kind}", status_code=204)
async def delete_register(
    stocktake_id: uuid.UUID,
    kind: str,
    user: CurrentUser = Depends(finance_perm),
    session: AsyncSession = Depends(get_session),
) -> Response:
    _kind(kind)
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    before = s.fa_registers.get(kind)
    if before is None:
        raise NotFound("Kỳ này chưa nhập sổ này.")
    await session.execute(
        delete(FaRegisterRow).where(FaRegisterRow.stocktake_id == s.id, FaRegisterRow.kind == kind)
    )
    s.fa_registers = {k: v for k, v in s.fa_registers.items() if k != kind}
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_register_deleted",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · sổ {kind.upper()}",
        before={k: before[k] for k in ("sha256", "rows", "total_cost")},
    )
    await session.commit()
    return Response(status_code=204)


class FaCodeIn(BaseModel):
    fa_code: str = Field("", max_length=128)


@router.patch("/stocktakes/{stocktake_id}/items/{item_id}/fa-code")
async def set_fa_code(
    stocktake_id: uuid.UUID,
    item_id: int,
    body: FaCodeIn,
    user: CurrentUser = Depends(manage_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Fix the Mã TSCĐ of an item (SDP's value may be missing or mistyped) so it joins the register."""
    s = await _get(session, stocktake_id, lock=True)
    _open_for_count(s)
    item = await session.get(StocktakeItem, item_id)
    if item is None or item.stocktake_id != s.id:
        raise NotFound("Không tìm thấy tài sản trong kỳ kiểm kê.")
    before = item.fa_code
    item.fa_code = body.fa_code.strip() or None
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.stocktake_fa_code_set",
        target_type="stocktake",
        target_id=str(s.id),
        target_label=f"{s.name} · {item.name} ({item.asset_tag or item.serial or '—'})",
        before={"fa_code": before},
        after={"fa_code": item.fa_code},
    )
    await session.commit()
    return _item_out(item, s.rules_version)
