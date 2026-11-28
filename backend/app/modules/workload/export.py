"""Excel report of Infra Support tasks: a per-operator summary of the backlog plus the task list."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from app.core.timeutil import TZ
from app.core.xlsx import header, safe
from app.modules.workload.models import Ticket

PHASE_LABEL = {"pending": "Chưa bắt đầu", "in_progress": "Đang thực hiện", "on_hold": "Tạm dừng"}
STATE_LABEL = {"done": "Đã thực hiện", "cancelled": "Đã huỷ"}
DATE_FMT = "dd/mm/yyyy hh:mm"
UNASSIGNED = "Chưa giao"

SUMMARY_HEADERS = [
    "Người xử lý",
    "Đang mở",
    "Chưa bắt đầu",
    "Đang thực hiện",
    "Tạm dừng",
    "Quá hạn",
    "Trễ đến 7 ngày",
    "Trễ 8–30 ngày",
    "Trễ trên 30 ngày",
    "Tồn lâu nhất (ngày)",
]
DETAIL_HEADERS = [
    "Request",
    "Tiêu đề",
    "Người yêu cầu",
    "Người xử lý",
    "Nhóm",
    "Tiến độ",
    "Trạng thái SDP",
    "Ưu tiên",
    "Ngày tạo",
    "Hạn xử lý",
    "Số ngày trễ",
    "Số ngày tồn",
    "Hoàn tất",
]


def _text(v: str | None) -> str:
    return safe(v or "")


def _local(d: datetime | None) -> datetime | None:
    return d.astimezone(TZ).replace(tzinfo=None) if d else None


def _days(start: datetime | None, end: datetime) -> int | None:
    return (end - start).days if start else None


def days_late(t: Ticket, now: datetime) -> int | None:
    """Whole days past the due date (0 = late by less than a day); None when not late."""
    if t.due_by is None:
        return None
    end = now if t.state == "open" else t.completed_at
    if end is None or end <= t.due_by:
        return None
    return (end - t.due_by).days


def summary_rows(tickets: list[Ticket], now: datetime) -> list[list[Any]]:
    """One row per operator over open tasks, the busiest first, then a total row."""
    per: dict[str, list[int]] = {}
    for t in tickets:
        if t.state != "open":
            continue
        r = per.setdefault(t.technician_name or UNASSIGNED, [0] * 9)
        r[0] += 1
        r[1 + ["pending", "in_progress", "on_hold"].index(t.phase or "pending")] += 1
        late = days_late(t, now)
        if late is not None:
            r[4] += 1
            r[5 if late <= 7 else 6 if late <= 30 else 7] += 1
        r[8] = max(r[8], _days(t.created_at, now) or 0)
    rows = sorted(per.items(), key=lambda kv: (kv[0] == UNASSIGNED, -kv[1][4], -kv[1][0], kv[0]))
    total = [sum(v[i] for _, v in rows) for i in range(8)] + [max((v[8] for _, v in rows), default=0)]
    return [[name, *v] for name, v in rows] + [["Tổng cộng", *total]]


def detail_row(t: Ticket, now: datetime) -> list[Any]:
    progress = (
        PHASE_LABEL.get(t.phase or "pending") if t.state == "open" else STATE_LABEL.get(t.state, t.state)
    )
    return [
        _text(f"#{t.display_id or t.id}"),
        _text(t.subject),
        _text(t.requester_name),
        _text(t.technician_name) or UNASSIGNED,
        _text(t.group_name),
        progress,
        _text(t.status),
        _text(t.priority),
        _local(t.created_at),
        _local(t.due_by),
        days_late(t, now),
        _days(t.created_at, now) if t.state == "open" else None,
        _local(t.completed_at),
    ]


def _sheet(ws: Worksheet, headers: list[str], rows: list[list[Any]], widths: list[int]) -> None:
    header(ws, headers, widths)
    for cell in ws[1]:
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, datetime):
                cell.number_format = DATE_FMT
    ws.freeze_panes = "B2"


def to_xlsx(tickets: list[Ticket], now: datetime, title: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Tổng hợp tồn"
    _sheet(ws, SUMMARY_HEADERS, summary_rows(tickets, now), [26, 10, 13, 14, 10, 10, 13, 13, 15, 17])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    ws.append([])
    ws.append([f"Số liệu lúc {_local(now):%d/%m/%Y %H:%M} (giờ Việt Nam). Chỉ tính task đang mở."])  # type: ignore[union-attr]

    detail = wb.create_sheet("Chi tiết task")
    _sheet(
        detail,
        DETAIL_HEADERS,
        [detail_row(t, now) for t in tickets],
        [10, 50, 24, 24, 20, 15, 22, 14, 17, 17, 12, 12, 17],
    )
    detail.auto_filter.ref = detail.dimensions
    wb.properties.title = title
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
