"""Year-end IT asset stocktake (kiểm kê): pure rules and the Excel file for Hành chánh / Kế toán."""

from __future__ import annotations

import io
from typing import Any, Literal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

Count = Literal["pending", "found", "missing", "extra"]
Condition = Literal["good", "damaged", "broken"]
Resolution = Literal["update_sdp", "add_to_books", "repair", "dispose", "investigate", "no_action"]

COUNT_LABEL = {"pending": "Chưa kiểm", "found": "Có", "missing": "Không tìm thấy", "extra": "Ngoài sổ SDP"}
CONDITION_LABEL = {"good": "Tốt", "damaged": "Hư hỏng nhẹ", "broken": "Hỏng, không dùng được"}
RESOLUTION_LABEL = {
    "update_sdp": "Cập nhật SDP",
    "add_to_books": "Ghi tăng tài sản (thêm vào SDP / sổ KT)",
    "repair": "Sửa chữa",
    "dispose": "Đề nghị thanh lý",
    "investigate": "Tiếp tục tìm / làm rõ",
    "no_action": "Không cần xử lý",
}


def norm_code(code: str | None) -> str:
    return (code or "").strip().upper()


def book_item(asset: dict[str, Any], fa_field: str) -> dict[str, Any]:
    """Item fields from a normalized SDP asset (lifecycle snapshot)."""
    user = asset.get("user_name") or None
    return {
        "asset_id": asset.get("id") or None,
        "name": (asset.get("name") or "")[:256],
        "product_type": asset.get("product_type"),
        "asset_tag": asset.get("asset_tag"),
        "serial": asset.get("serial"),
        "fa_code": str((asset.get("udf") or {}).get(fa_field) or "") or None if fa_field else None,
        "state": asset.get("state"),
        "user_name": user,
        "user_email": asset.get("user_email"),
        "site": asset.get("site"),
    }


def in_book(asset: dict[str, Any], disposed_state: str) -> bool:
    """Disposed assets are no longer on the books."""
    return (asset.get("state") or "").lower() != disposed_state.lower()


def matches(item: dict[str, Any], code: str) -> bool:
    """A scanned/typed code is the item's serial, asset tag or accounting code (case-insensitive)."""
    c = norm_code(code)
    return bool(c) and c in {norm_code(item.get(k)) for k in ("serial", "asset_tag", "fa_code")}


def difference(item: dict[str, Any]) -> str | None:
    """Why the item needs handling, or None when the books and the site agree."""
    if item["count"] == "missing":
        return "Không tìm thấy khi kiểm"
    if item["count"] == "extra":
        return "Có trên thực tế, không có trên SDP"
    if item["count"] == "found" and item.get("condition") in ("damaged", "broken"):
        return f"Tình trạng: {CONDITION_LABEL[item['condition']]}"
    return None


def summary(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {"book": 0, "pending": 0, "found": 0, "missing": 0, "extra": 0, "differences": 0, "unresolved": 0}
    for i in items:
        out[i["count"]] += 1
        if i["count"] != "extra":
            out["book"] += 1
        if difference(i):
            out["differences"] += 1
            if not i.get("resolution"):
                out["unresolved"] += 1
    return out


def _safe(v: Any) -> Any:
    return f"'{v}" if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v


HEADERS = ["STT", "Tên tài sản", "Loại", "Asset tag", "Serial", "Mã TSCĐ", "Trạng thái SDP", "Người dùng", "Site",
           "Kết quả kiểm", "Tình trạng", "Nơi thấy", "Ghi chú kiểm", "Chênh lệch", "Hướng xử lý", "Ghi chú xử lý",
           "Người kiểm"]  # fmt: skip
WIDTHS = [5, 30, 14, 14, 18, 14, 14, 24, 14, 14, 16, 18, 24, 30, 26, 30, 24]


def _row(n: int, i: dict[str, Any]) -> list[Any]:
    return [
        n,
        i["name"],
        i.get("product_type") or "",
        i.get("asset_tag") or "",
        i.get("serial") or "",
        i.get("fa_code") or "",
        i.get("state") or "",
        i.get("user_name") or "",
        i.get("site") or "",
        COUNT_LABEL[i["count"]],
        CONDITION_LABEL.get(i.get("condition") or "", ""),
        i.get("seen_at") or "",
        i.get("note") or "",
        difference(i) or "",
        RESOLUTION_LABEL.get(i.get("resolution") or "", ""),
        i.get("resolution_note") or "",
        i.get("counted_by") or "",
    ]


def _sheet(ws: Any, rows: list[dict[str, Any]]) -> None:
    ws.append(HEADERS)
    for n, i in enumerate(rows, 1):
        ws.append([_safe(v) for v in _row(n, i)])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E3F0F1")
    for n, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(n)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "C2"


def to_xlsx(stocktake: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """Tổng hợp · Sổ SDP (for Hành chánh / Kế toán to compare with their books) · Chênh lệch · Ngoài sổ SDP."""
    s = summary(items)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Tổng hợp"
    for row in [
        ["Kỳ kiểm kê", stocktake["name"]],
        ["Ngày chốt sổ", stocktake["cutoff"]],
        ["Dữ liệu SDP lúc", stocktake.get("source_synced_at") or ""],
        ["Trạng thái", stocktake["status_label"]],
        [],
        ["Tài sản trên sổ SDP", s["book"]],
        ["Có trên thực tế", s["found"]],
        ["Không tìm thấy", s["missing"]],
        ["Chưa kiểm", s["pending"]],
        ["Ngoài sổ SDP", s["extra"]],
        ["Chênh lệch cần xử lý", s["differences"]],
        ["Chưa có hướng xử lý", s["unresolved"]],
    ]:
        ws.append([_safe(v) for v in row])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 40
    for r in ws.iter_rows(max_col=1):
        r[0].font = Font(bold=True)
    _sheet(wb.create_sheet("Sổ SDP"), [i for i in items if i["count"] != "extra"])
    _sheet(wb.create_sheet("Chênh lệch"), [i for i in items if difference(i)])
    _sheet(wb.create_sheet("Ngoài sổ SDP"), [i for i in items if i["count"] == "extra"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
