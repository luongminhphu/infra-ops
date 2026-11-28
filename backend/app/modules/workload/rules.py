"""Pure rules turning an SDP request into a stored ticket (unit-tested, no I/O)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.modules.workload.quotes import fold
from app.modules.workload.settings import WorkloadSettings


def ms_to_dt(value: Any) -> datetime | None:
    raw = value.get("value") if isinstance(value, dict) else value
    return datetime.fromtimestamp(int(raw) / 1000, UTC) if raw and str(raw).isdigit() else None


def _name(value: Any) -> str | None:
    return (value or {}).get("name") if isinstance(value, dict) else None


# SDP's own status names (English and the Vietnamese UI): always finished / dropped, even when a saved
# configuration predates them – a closed case must never show as overdue.
BUILTIN_DONE = {
    "resolved", "closed", "completed", "đã xử lý", "đã đóng", "đã xong", "hoàn tất", "đã hoàn tất",
    "hoàn thành", "đã hoàn thành",
}  # fmt: skip
BUILTIN_CANCELLED = {"canceled", "cancelled", "rejected", "đã hủy", "đã huỷ", "huỷ", "hủy", "từ chối", "đã từ chối"}  # fmt: skip


def _key(text: str) -> str:
    return " ".join(fold(text).split())


def ticket_state(status: str, settings: WorkloadSettings) -> str:
    s = _key(status)
    if s in {_key(x) for x in BUILTIN_DONE} or s in {_key(x) for x in settings.done_statuses}:
        return "done"
    if s in {_key(x) for x in BUILTIN_CANCELLED} or s in {_key(x) for x in settings.cancelled_statuses}:
        return "cancelled"
    return "open"


def ticket_phase(status: str, state: str, settings: WorkloadSettings) -> str | None:
    """For open tasks only: in_progress | on_hold | pending."""
    if state != "open":
        return None
    s = _key(status)
    if s in {_key(x) for x in settings.in_progress_statuses}:
        return "in_progress"
    if s in {_key(x) for x in settings.on_hold_statuses}:
        return "on_hold"
    return "pending"


def device_need(text: str, settings: WorkloadSettings) -> str | None:
    """replace | upgrade | repair when the request text names a device need (first match wins)."""
    t = text.casefold()
    for kind in ("replace", "upgrade", "repair"):
        if any(k.casefold() in t for k in settings.device_keywords.get(kind, [])):
            return kind
    return None


def normalize_ticket(raw: dict[str, Any], settings: WorkloadSettings) -> dict[str, Any]:
    status = _name(raw.get("status")) or ""
    state = ticket_state(status, settings)
    text = " ".join(
        x
        for x in (str(raw.get("subject") or ""), _name(raw.get("category")), _name(raw.get("subcategory")))
        if x
    )
    tech = raw.get("technician") or {}
    completed = ms_to_dt(raw.get("completed_time")) or ms_to_dt(raw.get("resolved_time"))
    updated = (raw.get("last_updated_time") or {}).get("value")
    return {
        "id": str(raw["id"]),
        "display_id": str(raw.get("display_id") or raw["id"]),
        "subject": str(raw.get("subject") or "")[:500],
        "requester_name": _name(raw.get("requester")),
        "technician_id": str(tech["id"]) if tech.get("id") else None,
        "technician_name": tech.get("name"),
        "group_name": _name(raw.get("group")),
        "status": status[:64],
        "state": state,
        "phase": ticket_phase(status, state, settings),
        "device_need": device_need(text, settings),
        "priority": _name(raw.get("priority")),
        "created_at": ms_to_dt(raw.get("created_time")),
        "due_by": ms_to_dt(raw.get("due_by_time")),
        # Only finished tickets have a completion time (re-opened tickets drop it).
        "completed_at": completed if state == "done" else None,
        "sdp_updated_ms": int(updated) if updated and str(updated).isdigit() else None,
    }


def pick_bucket(days: int) -> str:
    """Chart granularity for a period length in days."""
    if days <= 31:
        return "day"
    if days <= 184:
        return "week"
    return "month"
