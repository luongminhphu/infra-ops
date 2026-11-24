"""Price rules: the 3-quote process for PC/Laptop proposals and reading prices from a vendor's PDF quote.

Pure functions (unit-tested); the API feeds them DB rows and extracted PDF text."""

from __future__ import annotations

import io
import re
import unicodedata
from typing import Any

from app.core.errors import AppError

MONEY = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d{5,})(?![\d.,]*\d)")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()


def needs_quotes(category: str, categories: list[str]) -> bool:
    """PC/Laptop-type items are priced from vendor quotes per proposal, not from the price list."""
    return fold(category).strip() in {fold(c).strip() for c in categories}


def quote_problem(prices: list[int], out_of_stock: int, threshold: int, required: int) -> str | None:
    """None when the quotes are enough to approve; otherwise what is missing (Vietnamese).

    A vendor with no stock (screenshot of its store) counts towards the required number of quotes,
    but at least one real price is always needed."""
    if not prices:
        return "Chưa có báo giá có giá – thêm báo giá của đối tác (kèm file PDF)."
    have = len(prices) + out_of_stock
    if min(prices) > threshold and have < required:
        limit = f"{threshold:,}".replace(",", ".")
        return f"Giá thấp nhất vượt {limit} đ – cần đủ {required} báo giá hoặc xác nhận không có hàng (đang có {have})."
    return None


def detect_supplier(text: str, suppliers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The supplier whose keyword appears in the quote (first match, keywords compared without accents)."""
    folded = fold(text)
    for sup in suppliers:
        if any(k.strip() and fold(k).strip() in folded for k in sup.get("keywords") or []):
            return sup
    return None


def pick_price(amounts: list[int], position: int) -> int | None:
    """0 = smallest amount (unit price < line total); N = the N-th amount from the left (vendor form)."""
    if not amounts:
        return None
    if position <= 0:
        return min(amounts)
    return amounts[position - 1] if position <= len(amounts) else None


def money_values(line: str) -> list[int]:
    return [int(re.sub(r"[.,]", "", m)) for m in MONEY.findall(line)]


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-z]+", fold(text)) if len(t) >= 2]


def match_prices(
    text: str, items: list[dict[str, Any]], min_price: int = 10_000, position: int = 0
) -> list[dict[str, Any]]:
    """Find each price-list item in the quote text and suggest a unit price.

    A line matches when most of the item's name tokens appear in it; the price is picked among the
    amounts ≥ min_price on that line (or the next line, when the PDF wraps) by the supplier's rule
    (default: the smallest, i.e. the unit price rather than the line total). Suggestions only: the
    supervisor confirms each one."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out = []
    for item in items:
        want = set(_tokens(item["name"]))
        if not want:
            continue
        best: tuple[float, int, str] | None = None
        for i, line in enumerate(lines):
            score = len(want & set(_tokens(line))) / len(want)
            if score < 0.6:
                continue
            amounts = [v for v in money_values(line) if v >= min_price] or [
                v for v in money_values(lines[i + 1] if i + 1 < len(lines) else "") if v >= min_price
            ]
            price = pick_price(amounts, position)
            if price and (best is None or score > best[0]):
                best = (score, price, line.strip())
        if best:
            out.append({"id": item["id"], "name": item["name"], "current_price": item.get("unit_price"),
                        "price": best[1], "line": best[2][:300], "score": round(best[0], 2)})  # fmt: skip
    return out


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:50])
    except Exception as exc:
        raise AppError("Không đọc được file PDF.", code="pdf_unreadable") from exc
    if not text.strip():
        raise AppError(
            "File PDF không có lớp chữ (bản scan/ảnh) – nhập giá tay trong Bảng giá.", code="pdf_no_text"
        )
    return text
