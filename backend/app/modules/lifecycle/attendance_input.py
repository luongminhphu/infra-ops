"""Employee lists HR sends for an attendance review: an Excel/CSV file, a table copied from an e-mail (tab
separated), or a plain list of IDs. Finds the employee-ID column (and the name, when there is one)."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AppError
from app.modules.lifecycle.parsing import fold

MAX_FILE_BYTES = 5 * 1024 * 1024
# Header cells (folded) that name the employee-ID / name column.
ID_HEADERS = ("ma nv", "msnv", "ma nhan vien", "ma so nhan vien", "ma so nv", "employee id", "emp id", "staff id",
              "employee code", "id nhan vien", "user id", "manv", "ma")  # fmt: skip
NAME_HEADERS = (
    "ho va ten",
    "ho ten",
    "ten nhan vien",
    "full name",
    "fullname",
    "employee name",
    "nhan vien",
    "name",
    "ten",
)
# Without a header row: an ID is 4–8 digits (row numbers are shorter, phone numbers longer, dates have "/").
BARE_ID = re.compile(r"^\d{4,8}$")
HEADER_ID = re.compile(r"^[0-9A-Za-z]{1,20}$")


@dataclass
class Parsed:
    items: list[dict[str, str]] = field(default_factory=list)  # {employee_id, name}
    skipped: int = 0  # data rows where no ID was recognised
    header: str | None = None  # the column the IDs came from, when a header row was found

    @property
    def ids(self) -> list[str]:
        return [i["employee_id"] for i in self.items]


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _norm_id(raw: str) -> str:
    v = raw.strip().strip("<>()[]{}\"'")
    # Excel drops leading zeros of numeric IDs ("3551" for 003551).
    return v.zfill(6) if v.isdigit() and len(v) < 6 else v


def rows_from_text(text: str) -> list[list[str]]:
    """A table copied from an e-mail or Excel has tab-separated cells; a plain list may use commas or spaces."""
    rows = []
    for line in text.replace("\r", "").split("\n"):
        if not line.strip():
            continue
        if "\t" in line:
            cells = line.split("\t")
        else:
            # "003551 Nguyễn Văn An, 003475 Trần B" → IDs and the text between them as separate cells.
            cells = [t for part in re.split(r"[,;]", line) for t in re.split(r"\s*\b(\d{4,8})\b\s*", part)]
        rows.append([c.strip() for c in cells if c.strip()])
    return rows


def rows_from_file(filename: str, data: bytes) -> list[list[str]]:
    if len(data) > MAX_FILE_BYTES:
        raise AppError("File quá lớn (tối đa 5 MB).", code="file_too_large")
    name = filename.lower()
    if name.endswith(".csv") or name.endswith(".txt"):
        text = data.decode("utf-8-sig", errors="replace")
        if "\t" in text.split("\n", 1)[0]:
            return rows_from_text(text)
        return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise AppError("Không đọc được file Excel (.xlsx).", code="xlsx_unreadable") from exc
        rows: list[list[str]] = []
        for ws in wb.worksheets:  # every sheet: HR sometimes splits departments
            for r in ws.iter_rows(values_only=True):
                cells = [_cell(v) for v in r]
                if any(cells):
                    rows.append(cells)
        return rows
    raise AppError(
        "Chỉ nhận file .xlsx hoặc .csv. File .xls cũ: mở bằng Excel và lưu lại dạng .xlsx.",
        code="unsupported_file",
    )


def _find_header(rows: list[list[str]]) -> tuple[int, int, int | None] | None:
    """(row index, ID column, name column) of the first header row among the first 10 rows."""
    for ri, row in enumerate(rows[:10]):
        folded = [re.sub(r"[^a-z0-9 ]", " ", fold(c)).strip() for c in row]
        folded = [re.sub(r"\s+", " ", c) for c in folded]
        id_col = next((ci for h in ID_HEADERS for ci, c in enumerate(folded) if c == h), None)
        if id_col is None:
            continue
        name_col = next(
            (ci for h in NAME_HEADERS for ci, c in enumerate(folded) if c == h and ci != id_col), None
        )
        return ri, id_col, name_col
    return None


def extract(rows: list[list[str]]) -> Parsed:
    """IDs in order, once each. With a header row: the ID column (and name); otherwise every 4–8 digit cell."""
    out = Parsed()
    seen: set[str] = set()

    def add(raw: str, name: str = "") -> bool:
        eid = _norm_id(raw)
        if not eid:
            return False
        if eid not in seen:
            seen.add(eid)
            out.items.append({"employee_id": eid, "name": name.strip()})
        return True

    head = _find_header(rows)
    if head is not None:
        ri, id_col, name_col = head
        out.header = rows[ri][id_col]
        for row in rows[ri + 1 :]:
            raw = row[id_col] if id_col < len(row) else ""
            name = row[name_col] if name_col is not None and name_col < len(row) else ""
            if not (HEADER_ID.match(_norm_id(raw)) and add(raw, name)):
                out.skipped += 1
        return out
    for row in rows:
        found = [c for c in row if BARE_ID.match(c.strip())]
        if not found:
            out.skipped += 1
        # The name next to a single ID on the row (e.g. "003551  Nguyễn Văn An").
        name = (
            next((c for c in row if c and not any(ch.isdigit() for ch in c)), "") if len(found) == 1 else ""
        )
        for c in found:
            add(c, name)
    return out
