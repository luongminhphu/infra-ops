"""CSV / Excel export of the reconciliation table."""

from __future__ import annotations

import csv
import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.modules.lifecycle.reconcile import RULES

KIND_LABEL = {
    "onboard": "Onboard",
    "offboard": "Offboard",
    "transfer": "Thuyên chuyển",
    "probation_to_official": "Thử việc → chính thức",
}
SEVERITY_LABEL = {"high": "Cao", "medium": "Trung bình", "low": "Thấp"}
HEADERS = [
    "Mã NV",
    "Họ tên",
    "Phòng ban",
    "Loại biến động",
    "Ngày hiệu lực",
    "SDP",
    "Tài sản đang dùng",
    "Danh sách tài sản",
    "BioStar",
    "Số lệch",
    "Mức độ",
    "Chi tiết lệch",
]


def _date(iso: str | None) -> str:
    if not iso:
        return ""
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}"


def to_records(rows: list[dict[str, Any]]) -> list[list[Any]]:
    out = []
    for r in rows:
        cells = r.get("cells", {})
        issues = [i for i in r.get("issues", []) if not i.get("acked")]
        out.append(
            [
                r["employee_id"],
                r.get("full_name") or "",
                r.get("department") or "",
                KIND_LABEL.get(r.get("kind") or "", ""),
                _date(r.get("effective_date")),
                cells.get("sdp", {}).get("label", ""),
                cells.get("assets", {}).get("label", ""),
                cells.get("assets", {}).get("detail", ""),
                cells.get("biostar", {}).get("label", ""),
                len(issues),
                SEVERITY_LABEL.get(r.get("max_severity") or "", ""),
                "\n".join(f"[{RULES[i['rule']][2]}] {i['message']}" for i in issues),
            ]
        )
    return out


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADERS)
    for rec in to_records(rows):
        # Neutralise spreadsheet formula injection from external data.
        writer.writerow([f"'{v}" if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in rec])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel opens UTF-8 Vietnamese correctly


def to_xlsx(rows: list[dict[str, Any]], title: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Đối soát"
    ws.append(HEADERS)
    for rec in to_records(rows):
        ws.append([f"'{v}" if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in rec])
    header_fill = PatternFill("solid", fgColor="E3F0F1")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
    widths = [10, 26, 22, 18, 13, 22, 10, 40, 14, 9, 11, 70]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    wb.properties.title = title
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
