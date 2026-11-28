"""Accent-free text folding, in Python (``fold``) and as a PostgreSQL expression (``fold_sql``)."""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import ColumnElement, func


def fold(text: str) -> str:
    """Lower-case and strip Vietnamese diacritics (đ → d), collapsing whitespace, for tolerant comparisons."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return re.sub(r"\s+", " ", "".join(c for c in decomposed if not unicodedata.combining(c))).strip().lower()


def _translation() -> tuple[str, str]:
    src, dst = [], []
    for cp in [*range(0xC0, 0x250), *range(0x1E00, 0x1F00)]:
        c = chr(cp)
        f = fold(c)
        if len(f) == 1 and f != c.lower() and f.isascii():
            src.append(c)
            dst.append(f)
    return "".join(src) + "đĐ", "".join(dst) + "dd"


_FROM, _TO = _translation()


def fold_sql(col: ColumnElement[str]) -> ColumnElement[str]:
    """SQL twin of ``fold`` (no extension needed). Whitespace is NOT collapsed/stripped, and NFD input is not
    recomposed: match against the stored precomposed text with a needle already passed through ``fold``."""
    return func.lower(func.translate(col, _FROM, _TO))
