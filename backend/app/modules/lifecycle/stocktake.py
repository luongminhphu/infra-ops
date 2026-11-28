"""Year-end IT asset stocktake (kiểm kê): pure rules and the Excel file for Hành chánh / Kế toán."""

from __future__ import annotations

import io
from typing import Any, Literal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.core.branding.service import xlsx_logo

Count = Literal["pending", "found", "missing", "extra"]
Condition = Literal["good", "damaged", "broken"]
Resolution = Literal["update_sdp", "add_to_books", "repair", "dispose", "investigate", "no_action"]

COUNT_LABEL = {"pending": "Chưa kiểm", "found": "Có", "missing": "Không tìm thấy", "extra": "Ngoài sổ SDP"}
CONDITION_LABEL = {"good": "Tốt", "damaged": "Hư hỏng nhẹ", "broken": "Hỏng, không dùng được"}
RESOLUTION_LABEL = {
    "update_sdp": "Sửa SDP (làm tay)",
    "add_to_books": "Đề nghị KT ghi tăng",
    "repair": "Sửa chữa",
    "dispose": "Đề nghị thanh lý / ghi giảm",
    "investigate": "Tìm thêm (ghi rõ)",
    "no_action": "Không cần xử lý",
}
# Resolutions a kind of difference accepts (see diff_kind); anything else is refused.
ALLOWED_RESOLUTIONS: dict[str, tuple[str, ...]] = {
    "missing": ("investigate", "dispose", "update_sdp", "no_action"),
    "extra": ("add_to_books", "update_sdp", "no_action"),
    "broken": ("repair", "dispose", "no_action"),
}
# What "Áp hướng xử lý mặc định" suggests per kind of difference: (resolution, note).
DEFAULT_RESOLUTIONS = {
    "missing": ("investigate", "Không tìm thấy khi kiểm"),
    "extra": ("add_to_books", ""),
    "broken": ("dispose", ""),
}


def area_for(user: dict[str, Any] | None, areas: list[dict[str, Any]]) -> str | None:
    """The area (name) an asset is counted in, by its user: listed employee ID first, then department.

    Assets nobody uses go to the store area (Kho IT). None = not in any area yet.
    """
    if not user:
        return next((a["name"] for a in areas if a.get("is_store")), None)
    emp = (user.get("employee_id") or "").strip()
    if emp and (hit := next((a["name"] for a in areas if emp in a.get("employee_ids", [])), None)):
        return hit
    dept = (user.get("department") or "").strip().lower()
    return next(
        (a["name"] for a in areas if dept and dept in {d.lower() for d in a.get("departments", [])}), None
    )


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


def diff_kind(item: dict[str, Any], rules_version: int = 2) -> str | None:
    """missing | extra | broken (| damaged, rules_version 1 only), or None when the books and the site agree.

    rules_version 1 (stocktakes closed before 70b) counted light damage as a difference; 2 does not.
    """
    if item["count"] in ("missing", "extra"):
        return item["count"]
    if item["count"] == "found" and item.get("condition") == "broken":
        return "broken"
    if item["count"] == "found" and item.get("condition") == "damaged" and rules_version < 2:
        return "damaged"
    return None


def difference(item: dict[str, Any], rules_version: int = 2) -> str | None:
    """Why the item needs handling, or None when the books and the site agree."""
    kind = diff_kind(item, rules_version)
    if kind == "missing":
        return "Không tìm thấy khi kiểm"
    if kind == "extra":
        return "Có trên thực tế, không có trên SDP"
    return f"Tình trạng: {CONDITION_LABEL[item['condition']]}" if kind else None


def summary(items: list[dict[str, Any]], rules_version: int = 2) -> dict[str, int]:
    out = {"book": 0, "pending": 0, "found": 0, "missing": 0, "extra": 0, "differences": 0, "unresolved": 0}
    for i in items:
        out[i["count"]] += 1
        if i["count"] != "extra":
            out["book"] += 1
        if difference(i, rules_version):
            out["differences"] += 1
            # A suggested resolution (default applied by the system) still waits for a person to confirm it.
            kind = diff_kind(i, rules_version)
            if (
                not i.get("resolution")
                or i.get("resolution_suggested")
                or (kind in ALLOWED_RESOLUTIONS and i["resolution"] not in ALLOWED_RESOLUTIONS[kind])
            ):
                out["unresolved"] += 1
    return out


def _safe(v: Any) -> Any:
    return f"'{v}" if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v


HEADERS = ["STT", "Khu", "Tên tài sản", "Loại", "Asset tag", "Serial", "Mã TSCĐ", "Trạng thái SDP", "Người dùng",
           "Mã NV", "Phòng ban", "Site", "Kết quả kiểm", "Tình trạng", "Nơi thấy", "Ghi chú kiểm", "Chênh lệch", "Hướng xử lý", "Ghi chú xử lý",
           "Người kiểm"]  # fmt: skip
WIDTHS = [5, 14, 30, 14, 14, 18, 14, 14, 24, 10, 22, 14, 14, 16, 18, 24, 30, 26, 30, 24]


def _row(n: int, i: dict[str, Any], rules_version: int = 2) -> list[Any]:
    return [
        n,
        i.get("zone") or "Chưa phân khu",
        i["name"],
        i.get("product_type") or "",
        i.get("asset_tag") or "",
        i.get("serial") or "",
        i.get("fa_code") or "",
        i.get("state") or "",
        i.get("user_name") or "",
        i.get("employee_id") or "",
        i.get("department") or "",
        i.get("site") or "",
        COUNT_LABEL[i["count"]],
        CONDITION_LABEL.get(i.get("condition") or "", ""),
        i.get("seen_at") or "",
        i.get("note") or "",
        difference(i, rules_version) or "",
        (
            RESOLUTION_LABEL.get(i.get("resolution") or "", "")
            + (" (gợi ý)" if i.get("resolution_suggested") else "")
        )
        if i.get("resolution")
        else "",
        i.get("resolution_note") or "",
        i.get("counted_by") or "",
    ]


def _sheet(ws: Any, rows: list[dict[str, Any]], rules_version: int = 2) -> None:
    ws.append(HEADERS)
    for n, i in enumerate(rows, 1):
        ws.append([_safe(v) for v in _row(n, i, rules_version)])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E3F0F1")
    for n, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(n)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "D2"


FIN_COLS = [5, 30, 16, 20, 9, 16, 16, 9, 16, 16, 9, 16, 16, 40]
MONEY_FMT = "#,##0"


def _mau05(wb: Workbook, title: str, lines: list[dict[str, Any]], head: dict[str, Any], heading: str) -> None:
    ws = wb.create_sheet(title)
    last = len(FIN_COLS)
    for r, text in enumerate(
        (
            heading,
            f"Thời điểm kiểm kê {head['cutoff']}",
            "Số liệu tạm – kỳ chưa kết thúc kiểm"
            if head.get("status") == "counting"
            else "Giấy làm việc – Kế toán hoàn thiện",
        ),
        1,
    ):
        ws.cell(r, 1, text).font = Font(bold=r == 1, size=14 if r == 1 else 11)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last)
    top = ["STT", "Tên TSCĐ", "Mã số", "Nơi sử dụng", "Theo sổ kế toán", "", "", "Theo kiểm kê", "", "",
           "Chênh lệch", "", "", "Ghi chú"]  # fmt: skip
    sub = ["", "", "", "", "Số lượng", "Nguyên giá", "Giá trị còn lại"] + ["SL", "NG", "GTCL"] * 2 + [""]
    for c, (a, b) in enumerate(zip(top, sub, strict=True), 1):
        ws.cell(5, c, a)
        ws.cell(6, c, b)
    for c in (1, 2, 3, 4, last):
        ws.merge_cells(start_row=5, start_column=c, end_row=6, end_column=c)
    for c in (5, 8, 11):
        ws.merge_cells(start_row=5, start_column=c, end_row=5, end_column=c + 2)
    for row in ws.iter_rows(min_row=5, max_row=6):
        for cell in row:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E3F0F1")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for n, ln in enumerate(lines, 1):
        vals = [n, ln["name"], ln["code"], ln["place"]]
        for k in ("book", "counted", "diff"):
            vals += [ln[k]["qty"], ln[k]["cost"], ln[k]["net"]]
        ws.append([_safe(v) for v in vals] + [_safe(ln["note"])])
        r = ws.max_row
        ws.cell(r, 3).number_format = "@"  # codes are text: keep leading zeros
        for c in (6, 7, 9, 10, 12, 13):
            ws.cell(r, c).number_format = MONEY_FMT
        for cell in ws[r]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for n, w in enumerate(FIN_COLS, 1):
        ws.column_dimensions[get_column_letter(n)].width = w
    ws.freeze_panes = "E7"


def _simple(wb: Workbook, title: str, headers: list[str], rows: list[list[Any]]) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    for r in rows:
        ws.append([_safe(v) for v in r])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E3F0F1")
    for n in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(n)].width = 22
    for row in ws.iter_rows(min_row=2):
        row[0].number_format = "@"


def _finance_sheets(wb: Workbook, recon: dict[str, Any], head: dict[str, Any]) -> None:
    lines = recon["lines"]
    _mau05(wb, "Mẫu 05-TSCĐ", [x for x in lines if x["kind"] == "tscd"], head, "BIÊN BẢN KIỂM KÊ TSCĐ")
    _mau05(wb, "Đối chiếu CCDC", [x for x in lines if x["kind"] == "ccdc"], head, "BIÊN BẢN KIỂM KÊ CCDC")
    _simple(
        wb,
        "Sổ KT chưa có trên SDP",
        ["Mã số", "Sổ", "Tên", "Nơi sử dụng", "Số lượng", "Nguyên giá", "Giá trị còn lại"],
        [
            [
                x["code"],
                x["kind"].upper(),
                x["name"],
                x["place"],
                x["book"]["qty"],
                x["book"]["cost"],
                x["book"]["net"],
            ]
            for x in lines
            if x["status"] == "not_in_sdp"
        ],
    )
    _simple(
        wb,
        "Chưa có trong sổ KT",
        ["Asset tag", "Tên tài sản", "Serial", "Mã TSCĐ", "Nơi sử dụng", "Kết quả kiểm", "SL"],
        [
            [
                x["asset_tag"],
                x["name"],
                x["serial"],
                x["fa_code"],
                x["place"],
                COUNT_LABEL.get(x["count"], ""),
                x["qty"],
            ]
            for x in recon["off_register"]
        ],
    )


def zone_progress(
    zones: list[dict[str, Any]], items: list[dict[str, Any]], rules_version: int = 2
) -> list[dict[str, Any]]:
    """Per zone (in order, then "Chưa phân khu" when some assets have none): who counts it and how far."""
    rows = [{**z, **summary([i for i in items if i.get("zone_id") == z["id"]], rules_version)} for z in zones]
    if loose := [i for i in items if not i.get("zone_id")]:
        rows.append(
            {
                "id": None,
                "name": "Chưa phân khu",
                "is_store": False,
                "assignee_name": None,
                **summary(loose, rules_version),
            }
        )
    return rows


def to_xlsx(
    stocktake: dict[str, Any],
    items: list[dict[str, Any]],
    zones: list[dict[str, Any]] | None = None,
    logo: bytes | None = None,
    rules_version: int = 2,
    recon: dict[str, Any] | None = None,
) -> bytes:
    """Tổng hợp · Theo khu · Sổ SDP (for Hành chánh / Kế toán to compare with their books) · Chênh lệch · Ngoài sổ."""
    s = summary(items, rules_version)
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
    xlsx_logo(ws, logo)  # company logo above the summary
    if zones:
        zs = wb.create_sheet("Theo khu")
        zs.append(
            ["Khu", "Người kiểm", "Trên sổ", "Có", "Không tìm thấy", "Chưa kiểm", "Ngoài sổ", "Chênh lệch"]
        )
        for z in zone_progress(zones, items, rules_version):
            zs.append([_safe(z["name"]), _safe(z.get("assignee_name") or ""), z["book"], z["found"], z["missing"],
                       z["pending"], z["extra"], z["differences"]])  # fmt: skip
        for cell in zs[1]:
            cell.font = Font(bold=True)
        for col, w in zip("ABCDEFGH", (18, 26, 10, 10, 14, 10, 10, 12), strict=True):
            zs.column_dimensions[col].width = w
    _sheet(wb.create_sheet("Sổ SDP"), [i for i in items if i["count"] != "extra"], rules_version)
    _sheet(wb.create_sheet("Chênh lệch"), [i for i in items if difference(i, rules_version)], rules_version)
    _sheet(wb.create_sheet("Ngoài sổ SDP"), [i for i in items if i["count"] == "extra"], rules_version)
    if recon is not None:  # accounting figures: only for a requester with lifecycle.stocktake.finance
        _finance_sheets(wb, recon, stocktake)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
