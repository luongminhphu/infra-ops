"""Attendance report from BioStar access logs: HR asks to review attendance / overtime of some employees over a
period; IT reads every BioStar system and hands back an Excel file (daily first/last scan + raw log)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.core.branding.service import xlsx_logo
from app.core.timeutil import TZ
from app.core.xlsx import header, safe
from app.modules.lifecycle.attendance_input import extract, rows_from_text
from app.modules.lifecycle.normalize import id_key

MAX_DAYS = 62
MAX_EMPLOYEES = 500
WEEKDAY = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]


def parse_ids(text: str) -> list[str]:
    """Employee IDs from what HR sent: a plain list, or a table pasted from an e-mail / Excel (ID column found
    by its header, otherwise every 4–8 digit cell)."""
    return extract(rows_from_text(text)).ids


def is_success(event: dict[str, Any]) -> bool:
    """A successful 1:1 verification (4096–4351) or 1:N identification (4864–5119) = the person scanned in."""
    try:
        code = int((event.get("event_type_id") or {}).get("code"))
    except (TypeError, ValueError):
        return False
    return 4096 <= code < 4352 or 4864 <= code < 5120


def period(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """The period in Vietnam time, whole days."""
    return datetime.combine(date_from, time.min, TZ), datetime.combine(
        date_to + timedelta(days=1), time.min, TZ
    )


@dataclass
class Scan:
    employee_id: str
    name: str
    at: datetime  # Vietnam time
    device: str
    source: str


@dataclass
class Report:
    employee_ids: list[str]
    date_from: date
    date_to: date
    names: dict[str, str] = field(default_factory=dict)  # requested ID -> name on BioStar
    scans: list[Scan] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def match_users(employee_ids: list[str], users: list[dict[str, Any]]) -> dict[str, str]:
    """BioStar user_id -> requested employee ID, on the last 5 digits (as reconciliation does)."""
    wanted = {id_key(e): e for e in employee_ids}
    return {
        str(u["user_id"]): wanted[id_key(str(u["user_id"]))]
        for u in users
        if u.get("user_id") and id_key(str(u["user_id"])) in wanted
    }


def add_events(report: Report, events: list[dict[str, Any]], by_user: dict[str, str], source: str) -> None:
    for ev in events:
        user = ev.get("user_id") or {}
        emp = by_user.get(str(user.get("user_id") or ""))
        if emp is None or not is_success(ev) or not ev.get("datetime"):
            continue
        at = datetime.fromisoformat(str(ev["datetime"]).replace("Z", "+00:00")).astimezone(TZ)
        report.scans.append(
            Scan(emp, user.get("name") or "", at, (ev.get("device_id") or {}).get("name") or "", source)
        )


def daily_rows(report: Report) -> list[list[Any]]:
    """One row per employee and day of the period, days without a scan included (HR asks about those)."""
    by_day: dict[tuple[str, date], list[datetime]] = {}
    for s in report.scans:
        by_day.setdefault((s.employee_id, s.at.date()), []).append(s.at)
    rows: list[list[Any]] = []
    for emp in report.employee_ids:
        if emp in report.not_found:
            continue
        day = report.date_from
        while day <= report.date_to:
            times = sorted(by_day.get((emp, day), []))
            first, last = (times[0], times[-1]) if times else (None, None)
            hours = (
                round((last - first).total_seconds() / 3600, 2) if first and last and last > first else None
            )
            rows.append(
                [
                    emp,
                    report.names.get(emp, ""),
                    day,
                    WEEKDAY[day.weekday()],
                    first.strftime("%H:%M") if first else "",
                    last.strftime("%H:%M") if last and len(times) > 1 else "",
                    hours,
                    len(times),
                    "" if times else "Không có dữ liệu quẹt",
                ]
            )
            day += timedelta(days=1)
    return rows


def _sheet(ws: Worksheet, headers: list[str], rows: list[list[Any]], widths: list[int]) -> None:
    header(ws, headers, widths)
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, datetime):
                cell.number_format = "dd/mm/yyyy hh:mm:ss"
            elif isinstance(cell.value, date):
                cell.number_format = "dd/mm/yyyy"
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions


def to_xlsx(report: Report, logo: bytes | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Tổng hợp theo ngày"
    _sheet(
        ws,
        ["Mã NV", "Họ tên", "Ngày", "Thứ", "Giờ vào", "Giờ ra", "Số giờ", "Số lần quẹt", "Ghi chú"],
        daily_rows(report),
        [10, 28, 12, 10, 9, 9, 9, 11, 22],
    )
    xlsx_logo(ws, logo)
    log = wb.create_sheet("Log chi tiết")
    _sheet(
        log,
        ["Mã NV", "Họ tên", "Thời gian", "Thiết bị", "Hệ thống BioStar"],
        [
            [s.employee_id, safe(s.name), s.at.replace(tzinfo=None), safe(s.device), s.source]
            for s in sorted(report.scans, key=lambda s: (s.employee_id, s.at))
        ],
        [10, 28, 20, 30, 20],
    )
    notes = wb.create_sheet("Ghi chú")
    notes.column_dimensions["A"].width = 110
    for line in [
        f"Khoảng thời gian: {report.date_from:%d/%m/%Y} – {report.date_to:%d/%m/%Y} (giờ Việt Nam).",
        f"Số nhân viên yêu cầu: {len(report.employee_ids)}; tìm thấy trên BioStar: "
        f"{len(report.employee_ids) - len(report.not_found)}.",
        "Giờ vào / Giờ ra = lần quẹt thành công đầu tiên / cuối cùng trong ngày (chưa tính ca qua đêm).",
        *(f"Không tìm thấy trên BioStar: {e}" for e in report.not_found),
        *(f"Không đọc được: {e}" for e in report.errors),
    ]:
        notes.append([line])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
