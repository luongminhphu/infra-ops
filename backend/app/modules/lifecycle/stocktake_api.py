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
from app.core.db import get_session
from app.core.errors import AppError, Conflict, NotFound
from app.core.timeutil import fmt_date
from app.modules.lifecycle.models import Snapshot, Stocktake, StocktakeItem
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import MODULE_ID
from app.modules.lifecycle.settings import load as load_settings
from app.modules.lifecycle.stocktake import (
    RESOLUTION_LABEL,
    Condition,
    Resolution,
    book_item,
    difference,
    in_book,
    matches,
    summary,
    to_xlsx,
)

router = APIRouter(tags=["lifecycle"])
view_perm = require("lifecycle.stocktake.view")
count_perm = require("lifecycle.stocktake.count")
manage_perm = require("lifecycle.stocktake.manage")

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
        "site": i.site,
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
    assets = [
        s.data
        for s in (
            await session.scalars(
                select(Snapshot)
                .where(Snapshot.run_id == run.id, Snapshot.system == "sdp_asset")
                .order_by(Snapshot.id)
            )
        ).all()
        if in_book(s.data, settings.asset_state_disposed)
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
    seen: set[str] = set()
    for a in assets:
        item = book_item(a, settings.asset_fa_code_field)
        if not item["asset_id"] or item["asset_id"] in seen:
            continue
        seen.add(item["asset_id"])
        session.add(StocktakeItem(stocktake_id=s.id, count="pending", **item))
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
    return {**_stocktake_out(s), "summary": summary(items), "items": items}


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
    user: CurrentUser = Depends(view_perm),
    session: AsyncSession = Depends(get_session),
) -> Response:
    s = await _get(session, stocktake_id)
    items = [_item_out(i) for i in await _items(session, stocktake_id)]
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
        target_label=s.name,
    )
    await session.commit()
    return Response(
        to_xlsx(head, items),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="kiem-ke-{s.cutoff:%Y%m%d}.xlsx"'},
    )


@router.get("/stocktake-meta")
async def stocktake_meta(_: CurrentUser = Depends(view_perm)) -> dict[str, Any]:
    return {"resolutions": RESOLUTION_LABEL}
