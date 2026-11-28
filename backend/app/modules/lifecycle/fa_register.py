"""Accounting registers (sổ TSCĐ / sổ CCDC) from Kế toán: a pure xlsx parser and the match to stocktake items.

Money stays Decimal end to end. Nothing here touches the database."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

from app.core.errors import AppError
from app.modules.lifecycle.parsing import fold

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
MAX_COLS = 60  # a stray cell far to the right must not make every row 16k cells wide
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024
KINDS = ("tscd", "ccdc")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NUMERIC = ("quantity", "cost", "accumulated", "net")

# Header labels (folded, punctuation as spaces) per field. Matched by equality, never as a substring.
ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("ma tscd", "ma tai san", "ma ccdc", "ma so", "ma ts", "ma cong cu dung cu", "ma tai san co dinh",
             "so hieu tscd", "so the tscd", "so hieu tai san", "so the tai san", "so hieu ccdc", "ma the tscd"),
    "name": ("ten tscd", "ten tai san", "ten ccdc", "ten cong cu dung cu", "ten ts", "ten tai san co dinh"),
    "group": ("nhom tai san", "nhom tscd", "nhom ccdc", "loai tai san", "nhom"),
    "quantity": ("so luong", "sl"),
    "cost": ("nguyen gia", "gia tri ban dau", "nguyen gia tscd", "gia tri ccdc"),
    "accumulated": ("hao mon luy ke", "gia tri da phan bo", "gia tri hao mon luy ke", "khau hao luy ke"),
    "net": ("gia tri con lai", "gia tri con lai chua phan bo", "gtcl"),
    "in_use_date": ("ngay ghi tang", "ngay dua vao su dung", "ngay bat dau su dung", "ngay mua"),
    "department": ("bo phan su dung", "bo phan", "phong ban", "don vi su dung"),
    "life_months": ("thoi gian khau hao thang", "so ky phan bo", "thoi gian phan bo thang", "so thang khau hao"),
    "account": ("tai khoan", "tk"),
    "serial": ("serial", "so serial", "serial number", "so seri"),
}  # fmt: skip


@dataclass
class RegisterRow:
    row_no: int
    code: str
    code_norm: str
    name: str
    group: str = ""
    quantity: Decimal | None = None
    cost: Decimal | None = None
    accumulated: Decimal | None = None
    net: Decimal | None = None
    in_use_date: date | None = None
    department: str = ""
    life_months: int | None = None
    account: str = ""
    serial: str = ""


@dataclass
class Parsed:
    rows: list[RegisterRow] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)  # field → header text as found
    header_rows: int = 1
    sheet: str = ""
    sheets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    headers: list[dict[str, str]] = field(
        default_factory=list
    )  # {col, top, sub} per column, to choose by hand
    positions: dict[str, str] = field(
        default_factory=dict
    )  # field → column letter as read (ambiguous: absent)
    ambiguous: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # field → [{col, label}] >1 hit
    errors: list[dict[str, Any]] = field(default_factory=list)  # unreadable cells: {row, column, value}
    skipped: dict[str, int] = field(default_factory=dict)  # reason → rows left out (not data)

    @property
    def total_cost(self) -> Decimal:
        return sum((r.cost for r in self.rows if r.cost is not None), Decimal(0))


def norm_code(raw: object) -> str:
    """Join key: strip, upper, drop Excel's leading apostrophe; no zero padding."""
    return str(raw if raw is not None else "").strip().lstrip("'").strip().upper()


def _label(v: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", fold(str(v or ""))).strip()


_GROUPED = re.compile(r"^\d{1,3}([.,]\d{3})+$")


def parse_number(v: object) -> Decimal | None:
    """Numeric cells stay exact; text follows the Vietnamese rule: with both "." and "," the last one is the
    decimal mark; a lone separator followed by exactly 3 digits groups thousands; "(…)" is negative."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, Decimal, float)):
        d = Decimal(repr(v) if isinstance(v, float) else v)
        if not d.is_finite():
            raise ValueError(str(v))
        return d
    s = str(v).replace("\xa0", "").replace(" ", "").strip()
    if not s:
        return None
    if s in ("-", "–", "—"):  # accounting "nil"
        return Decimal(0)
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")  # "1.234-" is SAP
    s = s.strip("()-+")
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif _GROUPED.match(s) or s.count(".") > 1 or s.count(",") > 1:
        s = s.replace(".", "").replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(str(v)) from None
    if not d.is_finite():  # "NaN", "Infinity" parse as Decimals but are not money
        raise ValueError(str(v))
    return -d if neg else d


def parse_date(v: object) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(s)


def _text(v: object) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return "" if v is None else str(v).strip()


def col_letter(ci: int) -> str:
    out = ""
    ci += 1
    while ci:
        ci, r = divmod(ci - 1, 26)
        out = chr(65 + r) + out
    return out


def col_index(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _columns(top: list[Any], below: list[Any] | None) -> list[dict[str, Any]]:
    """Per column: the group label (top row; a merged cell's label runs to the right), the sub label and the
    header texts it can carry (either row or both joined)."""
    out: list[dict[str, Any]] = []
    group = ""
    for ci in range(max(len(top), len(below or []))):
        a = _text(top[ci] if ci < len(top) else None)
        b = _text(below[ci] if below and ci < len(below) else None)
        if below is not None:
            group = a or (group if b else "")
            a = group
        sub = b or a
        label = f"{a} – {b}" if a and b else sub
        out.append({"top": a if b else "", "sub": sub, "label": label,
                    "cands": list(dict.fromkeys(x for x in (a, b, label) if x))})  # fmt: skip
    return out


def _map_columns(
    top: list[Any],
    below: list[Any] | None,
    mapping: dict[str, str] | None = None,
    ambiguous: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, tuple[int, str]]:
    """field → (column index, header label). ``mapping`` (field → column letter picked by the user) overrides
    the aliases. A field whose label sits on several columns (opening/closing balance) takes the first and is
    listed in ``ambiguous`` unless the user chose."""
    cols = _columns(top, below)
    hits: dict[str, list[int]] = {}
    for ci, c in enumerate(cols):
        hit = next((f for cand in c["cands"] for f, al in ALIASES.items() if _label(cand) in al), None)
        if hit:
            hits.setdefault(hit, []).append(ci)
    found = {f: (cis[0], cols[cis[0]]["label"]) for f, cis in hits.items()}
    for f, letter in (mapping or {}).items():
        ci = col_index(letter)
        if ci < len(cols):
            found[f] = (ci, cols[ci]["label"])
            hits.pop(f, None)
    if ambiguous is not None:
        ambiguous.update({f: [{"col": col_letter(i), "label": cols[i]["label"]} for i in cis]
                          for f, cis in hits.items() if len(cis) > 1})  # fmt: skip
    return found


def _is_continuation(row: list[Any], top: list[Any]) -> bool:
    """The second header row: no number/date cells and it names at least one more column."""
    typed = any(isinstance(v, (int, float, Decimal, date)) and not isinstance(v, bool) for v in row)
    return not typed and len(_map_columns(top, row)) > len(_map_columns(top, None))


def _is_column_numbers(row: list[Any]) -> bool:
    """The "A B C / 1 2 3 4 5" helper row some books print under the header."""
    cells = [_text(v) for v in row if _text(v)]
    nums = [int(c) for c in cells if c.isdigit()]
    return (
        len(nums) >= 3
        and all(c.isdigit() or (len(c) == 1 and c.isalpha()) for c in cells)
        and all(b - a == 1 for a, b in pairwise(nums))
    )


_SIGNATURE = ("người lập", "kế toán trưởng", "giám đốc", "thủ trưởng", "người ghi sổ")


def _skip_reason(code: str, name: str) -> str | None:
    """Why a row is not data. Compared with diacritics: "Công cụ" (tools) is not "Cộng" (sum)."""
    for c in (code, name):
        t = unicodedata.normalize("NFC", c).lower().strip(" :.")
        if t == "tổng" or t.startswith(("cộng", "tổng cộng", "tổng số")):
            return "total"
        if t.startswith(_SIGNATURE):
            return "signature"
    return None


def parse_register(data: bytes, sheet: str | None = None, mapping: dict[str, str] | None = None) -> Parsed:
    if len(data) > MAX_FILE_BYTES:
        raise AppError("File quá lớn (tối đa 5 MB).", code="file_too_large")
    if not data.startswith(b"PK\x03\x04"):
        raise AppError("Chỉ nhận file Excel .xlsx.", code="unsupported_file")
    for f, letter in (mapping or {}).items():
        if f not in ALIASES or not re.fullmatch(r"[A-Z]{1,2}", letter):  # a column letter: A, B … AZ
            raise AppError("Cột chọn tay không hợp lệ.", code="register_mapping_invalid")
    from openpyxl import load_workbook

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            unzipped = sum(i.file_size for i in z.infolist())
        if unzipped > MAX_UNZIPPED_BYTES:  # a zip bomb: tiny file, huge content
            raise AppError("File Excel giải nén quá lớn.", code="file_too_large")
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        wbf = load_workbook(
            io.BytesIO(data), read_only=True, data_only=False
        )  # to spot formulas without a value
    except AppError:
        raise
    except Exception as exc:
        raise AppError("Không đọc được file Excel (.xlsx).", code="xlsx_unreadable") from exc
    out = Parsed(sheets=list(wb.sheetnames))
    if sheet and sheet not in wb.sheetnames:
        raise AppError("Không có sheet này trong file.", code="sheet_not_found")
    name_ = sheet or wb.worksheets[0].title
    out.sheet = name_
    rows = [list(r) for r in wb[name_].iter_rows(values_only=True, max_row=MAX_ROWS + 25, max_col=MAX_COLS)]
    frows = [list(r) for r in wbf[name_].iter_rows(values_only=True, max_row=MAX_ROWS + 25, max_col=MAX_COLS)]

    head = None
    for i, row in enumerate(rows[:15]):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        cols = _map_columns(row, None, mapping)  # the code / name labels sit in the upper header row
        if "code" in cols and ("name" in cols or "cost" in cols):
            two = nxt is not None and _is_continuation(nxt, row)
            head = (
                i,
                _map_columns(row, nxt, mapping) if two else cols,
                2 if two else 1,
                row,
                nxt if two else None,
            )
            break
    if head is None:
        best = max(rows[:15], key=lambda r: sum(1 for v in r if _text(v)), default=[])
        raise AppError(
            "Không tìm thấy dòng tiêu đề có cột “Mã TSCĐ / Mã CCDC” và “Tên” hoặc “Nguyên giá”.",
            code="register_header_not_found",
            details={"headers": [t for v in best if (t := _text(v))]},
        )
    start, cols, out.header_rows, top, below = head
    out.ambiguous = {}
    _map_columns(top, below, mapping, out.ambiguous)
    out.columns = {f: h for f, (_, h) in cols.items()}
    out.positions = {f: col_letter(ci) for f, (ci, _) in cols.items() if f not in out.ambiguous}
    out.headers = [
        {"col": col_letter(i), "top": c["top"], "sub": c["sub"]}
        for i, c in enumerate(_columns(top, below))
        if c["sub"]
    ]
    if any(f not in cols or cols[f][0] != col_index(letter) for f, letter in (mapping or {}).items()):
        raise AppError("Cột chọn tay không có trong dòng tiêu đề.", code="register_mapping_invalid")
    for f in ("name", "cost"):
        if f not in cols:
            out.warnings.append(f"Không thấy cột {f}.")
    first = start + out.header_rows
    data_rows = rows[first:]
    if len(data_rows) > MAX_ROWS and any(any(c is not None for c in r) for r in data_rows[MAX_ROWS:]):
        raise AppError(f"Sổ quá dài (tối đa {MAX_ROWS:,} dòng).".replace(",", "."), code="register_too_long")

    def cell(row: list[Any], f: str) -> Any:
        ci = cols[f][0] if f in cols else None
        return row[ci] if ci is not None and ci < len(row) else None

    def skip(reason: str) -> None:
        out.skipped[reason] = out.skipped.get(reason, 0) + 1

    def bad(row_no: int, f: str, value: Any) -> None:
        out.errors.append({"row": row_no, "column": cols[f][1], "value": str(value)})

    seen: dict[str, int] = {}
    for off, row in enumerate(data_rows[:MAX_ROWS]):
        row_no = first + off + 1  # 1-based sheet row
        code, name = _text(cell(row, "code")), _text(cell(row, "name"))
        if not any(_text(v) for v in row):
            continue
        if _is_column_numbers(row):
            skip("column_numbers")
            continue
        if reason := _skip_reason(code, name):
            skip(reason)
            continue
        if not code:  # group headings, notes: not an asset, never part of the total
            skip("no_code")
            continue
        r = RegisterRow(row_no=row_no, code=code, code_norm=norm_code(code), name=name)
        for f in ("group", "department", "account", "serial"):
            setattr(r, f, _text(cell(row, f)))
        frow = frows[first + off] if first + off < len(frows) else []
        for f in (*NUMERIC, "life_months", "in_use_date"):
            if f not in cols:
                continue
            v = cell(row, f)
            if v is None and isinstance(fv := cell(frow, f), str) and fv.startswith("="):
                bad(row_no, f, fv)  # a formula Excel never calculated: the value is unknown, not zero
                continue
            try:
                if f == "in_use_date":
                    r.in_use_date = parse_date(v)
                elif f == "life_months":
                    life = parse_number(v)
                    r.life_months = int(life) if life is not None else None
                else:
                    setattr(r, f, parse_number(v))
            except ValueError:
                bad(row_no, f, v)
        if r.code_norm in seen:
            out.warnings.append(f"Dòng {row_no}: trùng mã {r.code} với dòng {seen[r.code_norm]}.")
        else:
            seen[r.code_norm] = row_no
        out.rows.append(r)
    if not out.rows:
        raise AppError("Sổ không có dòng dữ liệu nào.", code="register_empty")
    return out


MIN_SERIAL = 5  # "N/A", "0", "none" are not serials


def match_register(
    rows: list[dict[str, Any]], items: list[dict[str, Any]], kind: str
) -> list[dict[str, Any]]:
    """Per register code: its rows and the stocktake items. Item ↔ row by normalised code; a register serial
    finds the item only when it is long enough, unique among the items, and no item carries the register's code
    (and the item's own code is not in the register)."""
    by_code: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["code_norm"]:
            by_code.setdefault(r["code_norm"], []).append(r)
    items_by_code: dict[str, list[dict[str, Any]]] = {}
    items_by_serial: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        if c := norm_code(it.get("fa_code")):
            items_by_code.setdefault(c, []).append(it)
        if len(s := norm_code(it.get("serial"))) >= MIN_SERIAL and c not in by_code:
            items_by_serial.setdefault(s, []).append(it)
    out = []
    for code, rs in by_code.items():
        hit = items_by_code.get(code, [])
        if not hit:
            for r in rs:
                cand = items_by_serial.get(norm_code(r.get("serial")), [])
                if len(cand) == 1 and cand[0] not in hit:  # a serial shared by several items proves nothing
                    hit.append(cand[0])
        out.append({"code_norm": code, "kind": kind, "rows": rs, "items": hit})
    return out


# ---------------------------------------------------------------- three-way reconciliation (Sổ KT ↔ SDP ↔ thực tế)

_CENT = Decimal("0.01")
MONEY = ("cost", "net")


def _q(v: Decimal | None) -> Decimal | None:
    return None if v is None else v.quantize(_CENT)


def _sum(vals: list[Decimal | None]) -> Decimal | None:
    present = [v for v in vals if v is not None]
    return _q(sum(present, Decimal(0))) if present else None


def _triple(qty: Decimal | None, cost: Decimal | None, net: Decimal | None) -> dict[str, Decimal | None]:
    return {"qty": qty, "cost": _q(cost), "net": _q(net)}


def _zero(book: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
    """Zero where the book has a value, empty where it has none."""
    return {k: None if v is None else Decimal(0) for k, v in book.items()} | {"qty": Decimal(0)}


def _item_label(item: dict[str, Any]) -> str:
    return item.get("asset_tag") or item.get("name") or item.get("serial") or "?"


def _line(kind: str, rs: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    ln = _line_core(kind, rs, items)
    if _one_asset(rs):
        ln["note"] = "; ".join(x for x in (f"Sổ có {len(rs)} dòng cùng mã – Kế toán kiểm", ln["note"]) if x)
    return ln


def _one_asset(rs: list[dict[str, Any]]) -> bool:
    """Several rows under one code with no quantity are the parts of ONE asset (an upgrade), not N units."""
    return len(rs) > 1 and all(r.get("quantity") is None for r in rs)


def _line_core(kind: str, rs: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    first = rs[0]
    one_asset = _one_asset(rs)
    book_qty = (
        Decimal(1)
        if one_asset
        else sum(
            (Decimal(r["quantity"]) if r.get("quantity") is not None else Decimal(1) for r in rs), Decimal(0)
        )
    )
    book = _triple(book_qty, _sum([r.get("cost") for r in rs]), _sum([r.get("net") for r in rs]))
    place = first.get("department") or next((i.get("department") or i.get("zone") for i in items if i.get("department") or i.get("zone")), "")  # fmt: skip
    line: dict[str, Any] = {
        "kind": kind,
        "code": first["code"],
        "name": first.get("name") or next((i["name"] for i in items if i.get("name")), ""),
        "place": place or "",
        "book": book,
        "counted": _triple(None, None, None),
        "diff": _triple(None, None, None),
        "note": "",
        "items": len(items),
    }
    if not items:
        return {**line, "status": "not_in_sdp", "note": "Có trong sổ KT, không có trên SDP"}
    if any(i.get("count") == "pending" for i in items):  # not counted yet: neither found nor lost
        return {**line, "status": "pending", "note": "Chưa kiểm"}
    found = [i for i in items if i.get("count") in ("found", "extra")]
    missing = [i for i in items if not any(i is f for f in found)]
    if not found:  # nothing seen: counted 0, the whole book value is the difference
        zero = _zero(book)
        neg = {k: None if v is None else -v for k, v in book.items()}
        return {**line, "status": "missing_all", "counted": zero, "diff": neg}
    if book_qty > 1:  # several units under one code: count what was seen, value only when it splits evenly
        n = Decimal(len(found))
        counted = _triple(n, None, None)
        note = ""
        types = {i.get("product_type") for i in items}
        unit = {k: book[k] / book_qty if book[k] is not None else None for k in MONEY}
        even = len(types) == 1 and all(u is None or _q(u) * book_qty == book[k] for k, u in unit.items())
        if n == book_qty:
            return {**line, "status": "match", "counted": book, "diff": _zero(book)}
        if even:
            counted = _triple(n, *(None if unit[k] is None else _q(unit[k]) * n for k in MONEY))
        else:
            note = "Không chia đều được giá trị theo số lượng – Kế toán xác định giá trị"
        diff = _triple(n - book_qty, *(None if counted[k] is None or book[k] is None else counted[k] - book[k] for k in MONEY))  # fmt: skip
        return {**line, "status": "qty_diff", "counted": counted, "diff": diff, "note": note}
    if not missing:
        zero = _zero(book)
        return {**line, "status": "match", "counted": book, "diff": zero}
    # Some components are missing: the book values stay, Kế toán values the loss.
    return {
        **line,
        "status": "missing_part",
        "counted": book,
        "diff": _triple(Decimal(0), None, None),
        "note": f"Thiếu thành phần: {', '.join(_item_label(i) for i in missing)} – Kế toán xác định giá trị",
    }


def reconcile_registers(
    rows: list[dict[str, Any]], items: list[dict[str, Any]], rules_version: int = 2
) -> dict[str, Any]:
    """Sổ KT ↔ SDP ↔ thực tế. ``rows`` carry ``kind`` (tscd|ccdc); returns lines, off_register and warnings.

    One line per code and kind. An item counts as seen when its count is found/extra; ``rules_version`` is
    accepted for symmetry with the stocktake rules (condition does not change whether an asset exists).
    An item matched to the TSCĐ register is not offered to the CCDC one. ``off_register`` (items matched by no
    register, quantity only, never money) is judged only when BOTH registers are imported: with one, the item
    may simply sit in the other, so ``off_register_complete`` is false and the list is empty."""
    lines: list[dict[str, Any]] = []
    warnings: list[str] = []
    matched: set[int] = set()
    codes: dict[str, set[str]] = {}
    for kind in KINDS:
        kind_rows = [r for r in rows if r.get("kind", "tscd") == kind]
        codes[kind] = {r["code_norm"] for r in kind_rows if r["code_norm"]}
        for m in match_register(kind_rows, [i for i in items if id(i) not in matched], kind):
            matched.update(id(i) for i in m["items"])
            lines.append(_line(kind, m["rows"], m["items"]))
    both = sorted(codes["tscd"] & codes["ccdc"])
    if both:
        warnings.append(f"Mã có ở cả sổ TSCĐ và sổ CCDC (chỉ khớp ở sổ TSCĐ): {', '.join(both[:20])}")
    complete = all(codes[k] for k in KINDS)
    off = [
        {
            "name": i.get("name") or "",
            "asset_tag": i.get("asset_tag") or "",
            "serial": i.get("serial") or "",
            "fa_code": i.get("fa_code") or "",
            "place": i.get("department") or i.get("zone") or "",
            "count": i.get("count"),
            "qty": 1 if i.get("count") in ("found", "extra") else 0,
        }
        for i in items
        if complete and id(i) not in matched
    ]
    return {"lines": lines, "off_register": off, "off_register_complete": complete, "warnings": warnings}


def register_totals(recon: dict[str, Any]) -> dict[str, int]:
    """Counts only (no money) for the signed record: per kind matched / missing / partial, plus off_register
    (assets actually seen but in no register). A pending line counts as partial: a record exists only once
    every asset is counted."""
    out = {f"{k}_{s}": 0 for k in KINDS for s in ("matched", "missing", "partial")}
    for ln in recon["lines"]:
        s = {"match": "matched", "missing_all": "missing", "not_in_sdp": "missing"}.get(
            ln["status"], "partial"
        )
        out[f"{ln['kind']}_{s}"] += 1
    return {**out, "off_register": sum(o["qty"] for o in recon["off_register"])}


def jsonable(v: Any) -> Any:
    """Decimals as strings (never floats) for the API."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    return v
