"""Turn raw SDP / BioStar payloads into small, stable dicts stored in snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.modules.lifecycle.parsing import name_key


def _get_path(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name") or value.get("display_value")
    return value if isinstance(value, str) else None


def _sdp_time(value: Any) -> str | None:
    """SDP times are {"value": "<epoch ms>", "display_value": "..."}."""
    if isinstance(value, dict) and str(value.get("value", "")).isdigit():
        return datetime.fromtimestamp(int(value["value"]) / 1000, UTC).isoformat()
    return None


def sdp_user(raw: dict[str, Any], employee_id_field: str) -> dict[str, Any]:
    emp = _get_path(raw, employee_id_field)
    return {
        "id": str(raw.get("id", "")),
        "name": raw.get("name") or "",
        "email": raw.get("email_id"),
        "employee_id": str(emp).strip() if emp not in (None, "") else None,
        "department": _name(raw.get("department")),
        "job_title": raw.get("job_title"),
        "is_technician": bool(raw.get("is_technician", False)),
    }


def sdp_asset(raw: dict[str, Any]) -> dict[str, Any]:
    product = raw.get("product") or {}
    user = raw.get("user") or {}
    return {
        "id": str(raw.get("id", "")),
        "name": raw.get("name") or "",
        "asset_tag": raw.get("asset_tag"),
        "serial": raw.get("serial_number"),
        "product": _name(product),
        "product_type": _name(product.get("product_type") if isinstance(product, dict) else None)
        or _name(raw.get("product_type")),
        "state": _name(raw.get("state")),
        "user_id": str(user.get("id")) if isinstance(user, dict) and user.get("id") else None,
        "user_name": user.get("name") if isinstance(user, dict) else None,
        "user_email": user.get("email_id") if isinstance(user, dict) else None,
        "updated_at": _sdp_time(raw.get("last_updated_time")),
    }


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def _count(raw: dict[str, Any], *keys: str) -> int:
    for k in keys:
        v = raw.get(k)
        if isinstance(v, list):
            return len(v)
        if v not in (None, ""):
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return 0


EMPLOYEE_ID_DIGITS = 5


def id_key(employee_id: str) -> str:
    """The part of an employee ID compared across systems: its last 5 digits ("100008" → "00008")."""
    return employee_id[-EMPLOYEE_ID_DIGITS:] if employee_id.isdigit() else employee_id


def sdp_id_index(users: list[dict[str, Any]]) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    """Last 5 digits → the SDP (employee ID, name key) pairs sharing them."""
    by_key: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for u in users:
        if e := u.get("employee_id"):
            by_key.setdefault(id_key(e), []).append((e, name_key(u.get("name") or "")))
    return by_key


def resolve_employee_id(
    user_id: str, name: str, sdp_ids: dict[str, list[tuple[str, tuple[str, ...]]]]
) -> str:
    """Employee ID for a BioStar user: the SDP employee ID with the same last 5 digits (SDP is the reference).
    When several SDP IDs share those digits: the identical ID, else the one whose name matches; never a guess –
    otherwise the BioStar ID is kept as it is (and flagged by reconciliation)."""
    candidates = sdp_ids.get(id_key(user_id), [])
    ids = {e for e, _ in candidates}
    if len(ids) == 1:
        return next(iter(ids))
    if user_id in ids:
        return user_id
    named = {e for e, n in candidates if n and n == name_key(name)}
    return next(iter(named)) if len(named) == 1 else user_id


def biostar_user(raw: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    expiry = raw.get("expiry_datetime")
    expired = False
    if isinstance(expiry, str) and expiry:
        try:
            expired = datetime.fromisoformat(expiry.replace("Z", "+00:00")) < now
        except ValueError:
            expired = False
    disabled = _truthy(raw.get("disabled"))
    groups = raw.get("access_groups") or []
    return {
        "user_id": str(raw.get("user_id", "")).strip(),
        "name": raw.get("name") or "",
        "group": _name(raw.get("user_group_id")) or _name(raw.get("user_group")),
        "access_groups": [g.get("name") for g in groups if isinstance(g, dict) and g.get("name")],
        "disabled": disabled,
        "expired": expired,
        "active": not disabled and not expired,
        "start": raw.get("start_datetime"),
        "expiry": expiry,
        "cards": _count(raw, "card_count", "cards"),
        "fingerprints": _count(raw, "fingerprint_template_count", "fingerprint_templates"),
        "faces": _count(raw, "face_count", "visual_face_count", "faces"),
    }


def merge_biostar_users(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One user per employee ID across BioStar systems: an active user wins (an ex-employee still active anywhere
    must be flagged), otherwise the first read (main system first)."""
    by_emp: dict[str, dict[str, Any]] = {}
    for u in users:
        emp = u.get("employee_id") or u.get("user_id")
        if not emp:
            continue
        cur = by_emp.get(emp)
        if cur is None or (u["active"] and not cur["active"]):
            by_emp[emp] = u
    return list(by_emp.values())
