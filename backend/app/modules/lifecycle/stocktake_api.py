"""Year-end IT asset stocktake (kiểm kê) with Hành chánh / Kế toán: book from SDP, count on site, handle differences."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.branding.service import logo_bytes
from app.core.db import get_session
from app.core.documents import service as documents
from app.core.documents.models import Document
from app.core.errors import AppError, Conflict, NotFound
from app.core.timeutil import fmt_date
from app.modules.lifecycle.attendance_input import HEADER_ID, _norm_id
from app.modules.lifecycle.models import Snapshot, Stocktake, StocktakeArea, StocktakeItem, StocktakeZone
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import MODULE_ID
from app.modules.lifecycle.settings import load as load_settings
from app.modules.lifecycle.stocktake import (
    CONDITION_LABEL,
    COUNT_LABEL,
    RESOLUTION_LABEL,
    Condition,
    Resolution,
    area_for,
    book_item,
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

STATUS_LABEL = {"counting": "Đang kiểm", "reviewing": "Đang xử lý chênh lệch", "closed": "Đã đóng"}


def _item_out(i: StocktakeItem) -> dict[str, Any]:
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
    }
    out["difference"] = difference(out)
    return out


def _stocktake_out(s: Stocktake) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "cutoff": s.cutoff.isoformat(),
        "status": s.status,
        "status_label": STATUS_LABEL[s.status],
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
    stocktake_id: uuid.UUID, _: CurrentUser = Depends(view_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    s = await _get(session, stocktake_id)
    items = [_item_out(i) for i in await _items(session, stocktake_id)]
    zones = await _zones(session, stocktake_id)
    record = await session.get(Document, s.record_document_id) if s.record_document_id else None
    return {
        **_stocktake_out(s),
        "summary": summary(items),
        "zones": zone_progress(zones, items),
        "items": items,
        "record": _record_out(record),
    }


class ScanIn(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    seen_at: str = Field("", max_length=256)


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
    items = await _items(session, stocktake_id)
    hits = [i for i in items if matches(_item_out(i), body.code)]
    if not hits:
        return {"matched": False, "item": None, "already": False}
    if len(hits) > 1:
        names = ", ".join(f"{i.name} ({i.asset_tag or i.serial or '—'})" for i in hits[:5])
        raise Conflict(f"Mã “{body.code.strip()}” khớp {len(hits)} tài sản: {names}. Tick trực tiếp trong danh sách.",
                       code="ambiguous_code")  # fmt: skip
    item = hits[0]
    already = item.count in ("found", "extra")
    if not already:
        before = {"count": item.count}
        item.count, item.condition = "found", item.condition or "good"
        item.seen_at = body.seen_at.strip() or item.seen_at
        item.counted_by, item.counted_at = user.upn, _now()
        await record(
            session,
            actor=user.actor,
            module_id=MODULE_ID,
            action="lifecycle.stocktake_counted",
            target_type="stocktake",
            target_id=str(s.id),
            target_label=f"{s.name} · {item.name} ({item.asset_tag or item.serial or item.asset_id})",
            before=before,
            after={"count": "found", "code": body.code.strip()},
        )
        await session.commit()
    return {"matched": True, "item": _item_out(item), "already": already}


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
    if item.count == "extra":
        raise Conflict("Tài sản ngoài sổ: sửa ghi chú hoặc xoá dòng rồi thêm lại.", code="extra_item")
    if s.status == "reviewing" and body.count == "pending":
        raise Conflict("Đã kết thúc kiểm: chọn Có hoặc Không tìm thấy.", code="count_finished")
    before = {"count": item.count, "condition": item.condition, "seen_at": item.seen_at, "note": item.note}
    item.count = body.count
    item.condition = (body.condition or "good") if body.count == "found" else None
    item.seen_at, item.note = body.seen_at.strip(), body.note.strip()
    item.counted_by, item.counted_at = (user.upn, _now()) if body.count != "pending" else (None, None)
    if not difference(_item_out(item)):
        item.resolution, item.resolution_note, item.resolved_by, item.resolved_at = None, "", None, None
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
    return _item_out(item)


class ExtraIn(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    product_type: str = Field("", max_length=128)
    serial: str = Field("", max_length=128)
    asset_tag: str = Field("", max_length=128)
    fa_code: str = Field("", max_length=128)
    condition: Condition = "good"
    seen_at: str = Field("", max_length=256)
    note: str = Field("", max_length=1000)


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
    if not (body.serial.strip() or body.asset_tag.strip() or body.fa_code.strip()):
        raise AppError(
            "Nhập ít nhất serial, asset tag hoặc mã TSCĐ để nhận diện tài sản.", code="extra_no_code"
        )
    for i in await _items(session, stocktake_id):
        if any(matches(_item_out(i), c) for c in (body.serial, body.asset_tag, body.fa_code) if c.strip()):
            raise Conflict(f"Mã đã có trong kỳ kiểm kê: {i.name} ({i.asset_tag or i.serial or '—'}).",
                           code="extra_duplicate")  # fmt: skip
    item = StocktakeItem(
        stocktake_id=s.id,
        asset_id=None,
        name=body.name.strip(),
        product_type=body.product_type.strip() or None,
        serial=body.serial.strip() or None,
        asset_tag=body.asset_tag.strip() or None,
        fa_code=body.fa_code.strip() or None,
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
    return _item_out(item)


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
    if not difference(_item_out(item)):
        raise Conflict("Tài sản này khớp sổ, không cần hướng xử lý.", code="no_difference")
    if body.resolution == "investigate" and not body.note.strip():
        raise AppError("Ghi rõ cần tìm hoặc làm rõ điều gì.", code="resolution_note_required")
    before = {"resolution": item.resolution, "note": item.resolution_note}
    item.resolution, item.resolution_note = body.resolution, body.note.strip()
    item.resolved_by, item.resolved_at = (user.upn, _now()) if body.resolution else (None, None)
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
    return _item_out(item)


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
    items = [_item_out(i) for i in await _items(session, stocktake_id)]
    if left := summary(items)["unresolved"]:
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
        after={"status": "closed", **summary(items)},
    )
    await session.commit()
    return _stocktake_out(s)


@router.get("/stocktakes/{stocktake_id}/export")
async def export_stocktake(
    stocktake_id: uuid.UUID,
    user: CurrentUser = Depends(view_all_perm),
    session: AsyncSession = Depends(get_session),
    zone_id: str | None = None,
) -> Response:
    """The whole stocktake (file tổng: every zone together), or one zone's part (file thành phần, ?zone_id=;
    "none" = assets in no zone)."""
    s = await _get(session, stocktake_id)
    zones = await _zones(session, stocktake_id)
    names = {z["id"]: z["name"] for z in zones}
    items = [
        {**(o := _item_out(i)), "zone": names.get(o["zone_id"] or "")}
        for i in await _items(session, stocktake_id)
    ]
    part = ""
    if zone_id:
        if zone_id != "none" and zone_id not in names:
            raise NotFound("Không tìm thấy khu trong kỳ kiểm kê.")
        items = [i for i in items if (i["zone_id"] or "none") == zone_id]
        zones = [z for z in zones if z["id"] == zone_id]
        part = names.get(zone_id, "Chưa phân khu")
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
    )
    await session.commit()
    if part:
        head["name"] = f"{s.name} – {part}"
    return Response(
        to_xlsx(head, items, zones, await logo_bytes(session)),
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
    s: Stocktake, zones: list[dict[str, Any]], items: list[dict[str, Any]], body: RecordIn
) -> dict[str, Any]:
    names = {z["id"]: z["name"] for z in zones}
    totals = summary(items)
    diffs = []
    for i in items:
        if not (d := difference(i)):
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
        "date": datetime.now(UTC).date().isoformat(),
        "synced_at": fmt_date(s.source_synced_at) if s.source_synced_at else "",
        "totals": {
            "book": totals["book"],
            "found": totals["found"],
            "missing": totals["missing"],
            "extra": totals["extra"],
            "damaged": sum(
                1 for i in items if i["count"] == "found" and i.get("condition") in ("damaged", "broken")
            ),
            "differences": totals["differences"],
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
            for z in zone_progress(zones, items)
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
    items = [_item_out(i) for i in await _items(session, stocktake_id)]
    data = _record_data(s, await _zones(session, stocktake_id), items, body)
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
    return {"resolutions": RESOLUTION_LABEL}


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
    """Hand a zone to the person who counts it (None = nobody yet). Anyone with the count permission may still
    count assets of any zone: devices move between floors."""
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
    return _item_out(item)


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
