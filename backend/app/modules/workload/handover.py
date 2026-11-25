"""Read serial numbers (and asset tags) from a vendor's handover document: Excel or text PDF.

Pure functions; the operator reviews every filled value before anything is written to SDP.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from typing import Any

from app.core.errors import AppError
from app.modules.workload.quotes import fold, pdf_text

MAX_ROWS = 1000
_SERIAL_HEAD = ("serial", "s/n", "sn", "so seri", "seri")
_TAG_HEAD = ("asset tag", "ma tai san", "ma ts", "tag")
# "S/N: ABC123", "Serial Number: ...", "Số serial ...", "Seri: ..."
_LABELED = re.compile(
    r"(?:\bS/?N\b|\bSerial(?:\s*(?:Number|No\.?))?|\bS[ốoỐO]\s*ser(?:i|ial)|\bSeri)\s*[:#.\-]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-/]{3,40})",
    re.IGNORECASE,
)
# Table rows without labels: serial-like tokens (letters and digits, upper case).
_TOKEN = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,30}\b")


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # numeric serials typed into Excel
    return str(v).strip()


def _is_head(text: str, words: tuple[str, ...]) -> bool:
    t = fold(text).strip(" :.#")
    return any(t == w or t.startswith(w + " ") or (len(w) > 3 and w in t) for w in words)


def _valid(serial: str) -> bool:
    return 2 <= len(serial) <= 100 and not _is_head(serial, _SERIAL_HEAD)


def _dedupe(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out = []
    for r in rows:
        key = r["serial"].lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out[:MAX_ROWS]


def rows_from_xlsx(data: bytes) -> list[dict[str, str]]:
    """Rows under a "Serial" header column (any sheet); the other cells of the row are kept as text
    so the caller can match a serial to the right device."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise AppError("Không đọc được file Excel (.xlsx).", code="xlsx_unreadable") from exc
    rows: list[dict[str, str]] = []
    for ws in wb.worksheets:
        cols: tuple[int, int | None] | None = None
        for raw in ws.iter_rows(values_only=True):
            if len(rows) >= MAX_ROWS:
                break
            cells = [_cell(c) for c in raw]
            if cols is None:
                # A header row has several columns; a lone title ("DANH SÁCH SERIAL …") is not one.
                if sum(1 for c in cells if c) < 2:
                    continue
                si = next((k for k, c in enumerate(cells) if c and _is_head(c, _SERIAL_HEAD)), None)
                if si is not None:
                    ti = next(
                        (k for k, c in enumerate(cells) if c and k != si and _is_head(c, _TAG_HEAD)), None
                    )
                    cols = (si, ti)
                continue
            si, ti = cols
            serial = cells[si] if si < len(cells) else ""
            if not _valid(serial):
                continue
            tag = cells[ti] if ti is not None and ti < len(cells) else ""
            text = " ".join(c for k, c in enumerate(cells) if c and k not in (si, ti))
            rows.append({"serial": serial, "asset_tag": tag, "text": text})
    return _dedupe(rows)


def rows_from_text(text: str) -> list[dict[str, str]]:
    """Serials from PDF text: labeled values ("S/N: …") first; otherwise, below a "Serial" header,
    one serial-like token per line – tokens repeated on several lines are model names, not serials."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows: list[dict[str, str]] = []
    for ln in lines:
        if len(rows) >= MAX_ROWS:
            break
        rows += [
            {"serial": m.group(1), "asset_tag": "", "text": ln}
            for m in _LABELED.finditer(ln)
            if _valid(m.group(1)) and any(c.isdigit() for c in m.group(1))
        ]
    if rows:
        return _dedupe(rows)
    start = next((k for k, ln in enumerate(lines) if _is_head(ln, ("serial", "s/n", "so seri"))), None)
    if start is None:
        return []
    body = lines[start + 1 :]
    seen = Counter(t for ln in body for t in set(_TOKEN.findall(ln)))
    for ln in body:
        if len(rows) >= MAX_ROWS:
            break
        unique = [t for t in _TOKEN.findall(ln) if seen[t] == 1]
        if unique:
            rows.append({"serial": unique[-1], "asset_tag": "", "text": ln})
    return _dedupe(rows)


def read_handover(data: bytes, mime: str) -> list[dict[str, str]]:
    if mime == "application/pdf":
        try:
            text = pdf_text(data)
        except AppError as exc:
            if exc.code == "pdf_no_text":
                raise AppError(
                    "File PDF là bản scan (không có lớp chữ) – dùng file Excel hoặc nhập serial tay.",
                    code="pdf_no_text",
                ) from exc
            raise
        return rows_from_text(text)
    return rows_from_xlsx(data)
