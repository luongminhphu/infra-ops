"""Attendance report export (BioStar access logs)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.biostar import BioStarClient
from app.core.db import get_session
from app.core.errors import AppError
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
from app.modules.lifecycle.settings import MODULE_ID, main_biostar
from app.modules.lifecycle.settings import load as load_settings

router = APIRouter(tags=["lifecycle"])
export_perm = require("lifecycle.attendance.export")


class AttendanceIn(BaseModel):
    employee_ids: str = Field(min_length=1, max_length=20000)  # pasted list
    date_from: date
    date_to: date


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
    for which in reads:
        try:
            profile, ctx = await open_connection(session, "biostar", Purpose.READ, **which)
            async with BioStarClient(ctx) as client:
                users = await client.list_users()
                by_user = match_users(ids, users)
                for u in users:
                    if emp := by_user.get(str(u.get("user_id"))):
                        found.add(emp)
                        report.names.setdefault(emp, u.get("name") or "")
                if by_user:
                    add_events(
                        report, await client.search_events(sorted(by_user), start, end), by_user, profile.name
                    )
        except Exception as exc:
            report.errors.append(getattr(exc, "message", None) or str(exc))
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
        to_xlsx(report),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Not-Found": str(len(report.not_found)),
            "X-Read-Errors": str(len(report.errors)),
        },
    )
