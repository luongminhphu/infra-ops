"""Monthly offboarding review for HR (ISO 27001)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.db import get_session
from app.core.errors import AppError
from app.modules.lifecycle.offboard_review import build_eml, last_month, month_range, review_rows, to_xlsx
from app.modules.lifecycle.settings import MODULE_ID
from app.modules.lifecycle.settings import load as load_settings

router = APIRouter(tags=["lifecycle"])
review_perm = require("lifecycle.offboard_review.export")


def _month(month: str) -> date:
    try:
        y, m = (int(x) for x in month.split("-"))
        return date(y, m, 1)
    except ValueError as exc:
        raise AppError("Tháng không hợp lệ (yyyy-MM).", code="bad_month") from exc


@router.get("/offboard-review")
async def offboard_review(
    _: CurrentUser = Depends(review_perm),
    session: AsyncSession = Depends(get_session),
    month: str = Query("", max_length=7, description="yyyy-MM; default: last month"),
) -> dict[str, Any]:
    m = _month(month) if month else last_month()
    rows = await review_rows(session, m)
    settings = await load_settings(session)
    first, last = month_range(m)
    return {
        "month": f"{m:%Y-%m}",
        "from": first.isoformat(),
        "to": last.isoformat(),
        "items": [r.to_dict() for r in rows],
        "pending": sum(1 for r in rows if not r.done),
        "hr_review_to": settings.hr_review_to,
    }


@router.get("/offboard-review/export")
async def export_offboard_review(
    user: CurrentUser = Depends(review_perm),
    session: AsyncSession = Depends(get_session),
    month: str = Query(..., max_length=7),
    format: Literal["xlsx", "eml"] = "eml",
) -> Response:
    m = _month(month)
    rows = await review_rows(session, m)
    settings = await load_settings(session)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.offboard_review_exported",
        target_type="offboard_review",
        target_id=f"{m:%Y-%m}",
        target_label=format,
        message=f"Tháng {m:%m/%Y}: {len(rows)} nhân sự, {format}",
    )
    await session.commit()
    name = f"ra-soat-nghi-viec-{m:%Y-%m}"
    if format == "xlsx":
        return Response(
            to_xlsx(rows, m),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )
    return Response(
        build_eml(rows, m, settings, user.display_name),
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="{name}.eml"'},
    )
