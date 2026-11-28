"""Shared spreadsheet helpers: formula-injection guard and the standard header row."""

from __future__ import annotations

from typing import Any

from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe(value: Any) -> Any:
    """Neutralise spreadsheet formula injection: prefix a leading = + - @ TAB CR with an apostrophe (strings only)."""
    return f"'{value}" if isinstance(value, str) and value[:1] in _DANGEROUS else value


def header(ws: Worksheet, headers: list[str], widths: list[int] | None = None) -> None:
    """Append the header row (bold, light fill) and optionally set column widths."""
    ws.append(headers)
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E3F0F1")
    for i, w in enumerate(widths or [], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
