"""Attendance report export (BioStar access logs)."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.branding.service import logo_bytes
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.biostar import BioStarClient
from app.core.db import get_session
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.timeutil import today_vn
from app.modules.lifecycle.attendance import (
    MAX_DAYS,
    MAX_EMPLOYEES,
    Report,
    add_events,
    match_users,
    parse_ids,
    period,
    to_xlsx,
)
from app.modules.lifecycle.attendance_input import MAX_FILE_BYTES, extract, rows_from_file, rows_from_text
from app.modules.lifecycle.settings import MODULE_ID, main_biostar
from app.modules.lifecycle.settings import load as load_settings

router = APIRouter(tags=["lifecycle"])
export_perm = require("lifecycle.attendance.export")
log = get_logger(__name__)
# Not-found IDs sent back in a header for the on-screen result (the full list is in the file's Ghi chú sheet).
SHOWN_NOT_FOUND = 50


def _read_error(source: str, exc: Exception) -> str:
    """A reader-facing reason why one BioStar could not be read (never a raw exception text)."""
    if isinstance(exc, AppError):
        return f"{source}: {exc.message}"
    log.warning("attendance_biostar_read_failed", source=source, error=repr(exc))
    return f"{source}: không kết nối được (mạng, địa chỉ hoặc tài khoản BioStar)"


class AttendanceIn(BaseModel):
    employee_ids: str = Field(min_length=1, max_length=20000)  # pasted list
    date_from: date
    date_to: date


@router.post("/attendance/parse")
async def parse_employee_list(
    file: UploadFile | None = File(None),
    text: str = Form("", max_length=200_000),
    _: CurrentUser = Depends(export_perm),
) -> dict[str, Any]:
    """Recognise the employees in HR's file or pasted table, for review before the report. Nothing is stored."""
    if file is not None and file.filename:
        data = await file.read(MAX_FILE_BYTES + 1)
        rows = await asyncio.to_thread(rows_from_file, file.filename, data)
    else:
        rows = rows_from_text(text)
    parsed = extract(rows)
    if not parsed.items:
        raise AppError(
            "Không nhận diện được Mã NV nào. Kiểm tra bảng có cột “Mã NV” hoặc mã 4–8 chữ số.",
            code="no_employee_ids",
        )
    return {"items": parsed.items[:MAX_EMPLOYEES], "total": len(parsed.items), "skipped": parsed.skipped,
            "column": parsed.header, "max": MAX_EMPLOYEES}  # fmt: skip


@router.get("/attendance/status")
async def attendance_status(
    _: CurrentUser = Depends(export_perm), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Whether the main BioStar can be opened, checked when the page opens (nothing is read from BioStar)."""
    try:
        await open_connection(session, "biostar", Purpose.READ, **main_biostar(await load_settings(session)))
    except AppError as exc:
        return {"ready": False, "message": exc.message}
    return {"ready": True, "message": None}


@router.post("/attendance/export")
async def export_attendance(
    body: AttendanceIn,
    user: CurrentUser = Depends(export_perm),
    session: AsyncSession = Depends(get_session),
) -> Response:
    ids = parse_ids(body.employee_ids)
    if not ids or len(ids) > MAX_EMPLOYEES or any(len(e) > 20 for e in ids):
        raise AppError(f"Nhập từ 1 đến {MAX_EMPLOYEES} Mã NV hợp lệ.", code="bad_employee_ids")
    if body.date_to < body.date_from or (body.date_to - body.date_from).days >= MAX_DAYS:
        raise AppError(f"Khoảng thời gian không hợp lệ (tối đa {MAX_DAYS} ngày).", code="bad_period")
    if body.date_from > today_vn():
        raise AppError("Ngày bắt đầu ở tương lai.", code="bad_period")

    settings = await load_settings(session)
    report = Report(ids, body.date_from, body.date_to)
    start, end = period(body.date_from, body.date_to)
    found: set[str] = set()
    reads = [main_biostar(settings)] + [{"profile_id": s.profile_id} for s in settings.biostar_sources]
    read_ok = 0
    sources: list[str] = []  # per system: how many requested IDs matched (last 5 digits) and scans read
    for i, which in enumerate(reads):
        source = "BioStar chính" if i == 0 else f"BioStar phụ {i}"
        try:
            profile, ctx = await open_connection(session, "biostar", Purpose.READ, **which)
            source = profile.name
            async with BioStarClient(ctx) as client:
                users = await client.list_users()
                by_user = match_users(ids, users)
                for u in users:
                    if emp := by_user.get(str(u.get("user_id"))):
                        found.add(emp)
                        report.names.setdefault(emp, u.get("name") or "")
                before = len(report.scans)
                if by_user:
                    add_events(
                        report, await client.search_events(sorted(by_user), start, end), by_user, profile.name
                    )
                sample = ", ".join(
                    f"{e}↔{u}" for u, e in sorted(by_user.items(), key=lambda x: x[1])[:3] if u != e
                )
                sources.append(
                    f"{profile.name}: {len(set(by_user.values()))}/{len(ids)} Mã NV khớp theo 5 số cuối"
                    + (f" (vd. {sample})" if sample else "")
                    + f", {len(report.scans) - before} lượt quẹt"
                )
            read_ok += 1
        except Exception as exc:
            report.errors.append(_read_error(source, exc))
    if report.errors and len(report.errors) == len(reads):
        raise AppError("Không đọc được BioStar: " + "; ".join(report.errors), code="biostar_unreadable")
    report.not_found = [e for e in ids if e not in found]

    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.attendance_exported",
        system_type="biostar",
        message=f"{len(ids)} Mã NV, {body.date_from:%d/%m/%Y}–{body.date_to:%d/%m/%Y}, {len(report.scans)} lượt quẹt",
    )
    await session.commit()
    name = f"cham-cong-{body.date_from:%Y%m%d}-{body.date_to:%Y%m%d}.xlsx"
    return Response(
        to_xlsx(report, await logo_bytes(session)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Requested": str(len(ids)),
            "X-Not-Found": str(len(report.not_found)),
            "X-Not-Found-Ids": ",".join(report.not_found[:SHOWN_NOT_FOUND]),
            "X-Read-Ok": str(read_ok),
            "X-Read-Errors": str(len(report.errors)),
            # Header values are latin-1: the (Vietnamese) reasons travel percent-encoded, one per line.
            "X-Read-Error-Detail": quote("\n".join(report.errors)[:1500]),
            "X-Source-Detail": quote("\n".join(sources)[:1500]),
        },
    )
