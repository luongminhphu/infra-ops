"""SDP users without an employee ID, with IDs suggested from same-name BioStar users (list + Excel)."""

from __future__ import annotations

import io
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Response
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.db import get_session
from app.core.errors import NotFound
from app.core.xlsx import header, safe
from app.modules.lifecycle.id_suggest import failed_sources, suggest
from app.modules.lifecycle.models import Snapshot
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import MODULE_ID

router = APIRouter(tags=["lifecycle"])
view = require("lifecycle.reconciliation.view")

STATUS_LABEL = {
    "matched": "Khớp tên",
    "near": "Gần đúng",
    "ambiguous": "Nhiều ứng viên",
    "claimed": "Mã đã thuộc user khác",
    "none": "Không tìm thấy",
    "unknown": "Không rõ (BioStar đọc lỗi)",
}
LEVEL_LABEL = {"exact": "Khớp tên", "near": "Gần đúng"}
HEADERS = [
    "Tên SDP",
    "Email",
    "Phòng ban",
    "Giá trị hiện tại",
    "Trạng thái",
    "Mã NV đề xuất (5 số cuối)",
    "Mã trên BioStar",
    "Tên BioStar",
    "Hệ thống BioStar",
    "Mức khớp",
    "Ghi chú",
    "Ứng viên khác",
]


async def _build(session: AsyncSession, only_candidates: bool) -> dict[str, Any]:
    run = await latest_run(session)
    if run is None:
        raise NotFound("Chưa có lần đồng bộ nào. Chạy đồng bộ trước.")
    errors = run.errors or {}
    sdp_failed, bs_failed = failed_sources(errors)
    out: dict[str, Any] = {
        "run": {"id": str(run.id), "finished_at": run.finished_at},
        "sources": {
            "sdp": "failed" if sdp_failed else "ok",
            "biostar": "failed" if bs_failed else "ok",
            "failed_systems": [
                k for k in errors if k in ("sdp", "sdp_users") or str(k).startswith("biostar")
            ],
        },
        "items": [],
        "counts": {},
        "total_missing": 0,
    }
    if sdp_failed:  # never present a failed read as "no user is missing an ID"
        return out

    async def load(system: str) -> list[dict[str, Any]]:
        rows = await session.scalars(
            select(Snapshot.data).where(Snapshot.run_id == run.id, Snapshot.system == system)
        )
        return list(rows)

    rows = suggest(await load("sdp_user"), [] if bs_failed else await load("biostar_user"))
    if bs_failed:
        for r in rows:
            r["status"] = "unknown"
    out["counts"] = dict(Counter(r["status"] for r in rows))
    out["total_missing"] = len(rows)
    # With BioStar unreadable every row is "unknown": filtering would show an empty list as if nothing is missing.
    out["items"] = [r for r in rows if bs_failed or not only_candidates or r["status"] != "none"]
    return out


def _candidate_line(c: dict[str, Any]) -> str:
    flags = [f for f, on in (("đã khoá", not c.get("active", True)), ("đã nghỉ", c.get("retired"))) if on]
    text = f"{c['employee_id']} (mã BioStar {c['biostar_user_id']}) - {c['biostar_name']} ({c['source']})"
    return text + "".join(f" [{f}]" for f in flags) + "".join(f" - {n}" for n in c["notes"])


def to_xlsx(items: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "User SDP thiếu Mã NV"
    header(ws, HEADERS, [26, 30, 22, 16, 22, 16, 16, 26, 20, 12, 50, 50])
    for r in items:
        s = r["suggestion"] or {}
        others = [c for c in r["candidates"] if c is not r["suggestion"]]
        notes = (
            s.get("notes", []) if r["suggestion"] else [n for c in r["candidates"][:1] for n in c["notes"]]
        )
        ws.append(
            [
                safe(v)
                for v in (
                    r["name"],
                    r["email"] or "",
                    r["department"] or "",
                    r["current_value"],
                    STATUS_LABEL[r["status"]],
                    s.get("employee_id", ""),
                    s.get("biostar_user_id", ""),
                    s.get("biostar_name", ""),
                    s.get("source") or "",
                    LEVEL_LABEL.get(s.get("level", ""), ""),
                    "; ".join(notes),
                    "\n".join(_candidate_line(c) for c in others),
                )
            ]
        )
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@router.get("/sdp-missing-ids")
async def sdp_missing_ids(
    only_candidates: bool = True,
    _: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await _build(session, only_candidates)


@router.get("/sdp-missing-ids.xlsx")
async def sdp_missing_ids_xlsx(
    only_candidates: bool = True,
    user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
) -> Response:
    data = await _build(session, only_candidates)
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.sdp_missing_ids_exported",
        message=f"{len(data['items'])} rows",
    )
    await session.commit()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return Response(
        to_xlsx(data["items"]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="user-sdp-thieu-ma-nv-{stamp}.xlsx"'},
    )
