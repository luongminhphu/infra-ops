"""Shared time helpers: the platform's local time zone, today's date and dd/MM/yyyy formatting."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def today_vn() -> date:
    return datetime.now(TZ).date()


def fmt_date(d: date | None, empty: str = "") -> str:
    """dd/MM/yyyy; an aware datetime is shown in local time. ``empty`` is returned for a missing value."""
    if not d:
        return empty
    if isinstance(d, datetime) and d.tzinfo is not None:
        d = d.astimezone(TZ)
    return d.strftime("%d/%m/%Y")
