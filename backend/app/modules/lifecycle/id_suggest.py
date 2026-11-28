"""Suggest employee IDs for SDP users that have none, from BioStar users with the same name (pure, no DB).

IDs are compared and proposed on their last 5 digits (`normalize.id_key`), never padded to 6.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.modules.lifecycle.normalize import id_key
from app.modules.lifecycle.parsing import fold, name_key

_VALID_ID = re.compile(r"^[0-9A-Za-z]{2,20}$")
_FIRST_WORD_DOT = re.compile(r"^\s*([^\s.]+)\.")
STATUS_ORDER = ("matched", "near", "ambiguous", "claimed", "none")
MAX_CANDIDATES = 3
_JUNK = {"na", "n/a", "none", "null", "tbd", "x", "-"}


def missing(emp: str | None) -> bool:
    """True for an empty or junk employee ID ("N/A", "none", "-", "000000")."""
    value = (emp or "").strip()
    return not _VALID_ID.match(value) or set(value) == {"0"} or fold(value) in _JUNK


def failed_sources(errors: dict[str, Any] | None) -> tuple[bool, bool]:
    """(SDP users read failed, any BioStar read failed) from a sync run's `errors` keys."""
    keys = [str(k) for k in errors or {}]
    return any(k in ("sdp", "sdp_users") for k in keys), any(k.startswith("biostar") for k in keys)


def given_name(name: str) -> str:
    """First word when written "Thinh. Nguyen Canh", otherwise the last word; folded."""
    if m := _FIRST_WORD_DOT.match(name):
        return fold(m.group(1))
    words = fold(name).split()
    return words[-1] if words else ""


class _Info:
    """Name facts computed once per user: key (sorted folded words), given name, accents, raw words."""

    def __init__(self, name: str) -> None:
        self.key = name_key(name)
        self.set = set(self.key)
        self.given = given_name(name)
        plain = re.sub(r"\s+", " ", unicodedata.normalize("NFC", name)).strip().lower()
        self.accented = fold(name) != plain
        self.words = sorted(re.findall(r"\w+", plain))


def _level(a: _Info, b: _Info) -> tuple[str, list[str]] | None:
    """("exact" | "near", notes) or None when the two names are not the same person."""
    if not a.key or not b.key:
        return None
    if a.key == b.key:
        if a.accented and b.accented and a.words != b.words:
            return "near", ["khác dấu"]
        return "exact", []
    small, big = (a.set, b.set) if len(a.set) < len(b.set) else (b.set, a.set)
    if small < big and len(small) >= 2 and a.given in (common := a.set & b.set) and b.given in common:
        return "near", ["thiếu tên đệm"]
    return None


def _label(u: dict[str, Any]) -> str:
    return " ".join(x for x in (u.get("name"), u.get("email")) if x)


def suggest(sdp_users: list[dict[str, Any]], biostar_users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per SDP user missing an employee ID, with <=3 ranked BioStar candidates."""
    claimed_by: dict[str, list[dict[str, Any]]] = {}
    for u in sdp_users:
        if not missing(u.get("employee_id")):
            claimed_by.setdefault(id_key(u["employee_id"].strip()), []).append(u)
    by_name: dict[tuple[str, ...], list[tuple[dict[str, Any], _Info]]] = {}
    by_given: dict[str, list[tuple[dict[str, Any], _Info]]] = {}
    for b in biostar_users:
        info = _Info(b.get("name") or "")
        by_name.setdefault(info.key, []).append((b, info))
        by_given.setdefault(info.given, []).append((b, info))

    rows = []
    for u in sdp_users:
        if not missing(u.get("employee_id")):
            continue
        ui = _Info(u.get("name") or "")
        # Exact names regardless of word order; the given-name bucket only adds near matches.
        pool = by_name.get(ui.key, []) + [x for x in by_given.get(ui.given, []) if x[1].key != ui.key]
        ranked = []
        for b, bi in pool:
            found = _level(ui, bi)
            if found is None:
                continue
            level, notes = found
            proposed = id_key(str(b.get("user_id") or ""))
            if not proposed:
                continue
            owners = claimed_by.get(proposed, [])
            claimed = bool(owners)
            if claimed:
                notes.append("Mã đã thuộc user SDP khác: " + ", ".join(_label(o) for o in owners))
            retired = bool(b.get("retired"))
            usable = bool(b.get("active")) and not retired
            if not b.get("active"):
                notes.append("User BioStar đã khoá/hết hạn")
            if retired:
                notes.append("Nhóm loại trừ/đã nghỉ")
            same_dept = bool(u.get("department")) and fold(u["department"]) == fold(b.get("group") or "")
            tier = (level != "exact", claimed, not usable)
            ranked.append(
                (
                    (*tier, not same_dept),
                    tier,
                    {
                        "employee_id": proposed,
                        "biostar_user_id": b.get("user_id"),
                        "biostar_name": b.get("name") or "",
                        "source": b.get("source"),
                        "level": level,
                        "notes": notes,
                        "active": bool(b.get("active")),
                        "retired": retired,
                    },
                    claimed,
                )
            )
        ranked.sort(key=lambda r: r[0])
        if not ranked:
            status = "none"
        elif sum(1 for r in ranked if r[1] == ranked[0][1]) >= 2:
            status = "ambiguous"
        elif ranked[0][3]:
            status = "claimed"
        elif ranked[0][2]["level"] == "near" or ranked[0][1][2]:  # near name, or top is inactive/retired
            status = "near"
        else:
            status = "matched"
        rows.append(
            {
                "sdp_user_id": u.get("id"),
                "name": u.get("name") or "",
                "email": u.get("email"),
                "department": u.get("department"),
                "current_value": u.get("employee_id") or "",
                "status": status,
                "suggestion": ranked[0][2] if status in ("matched", "near") else None,
                "candidates": [r[2] for r in ranked[:MAX_CANDIDATES]],
            }
        )
    _flag_shared_top(rows)
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), fold(r["name"])))
    return rows


def _flag_shared_top(rows: list[dict[str, Any]]) -> None:
    """Two SDP users whose top candidate is the same ID cannot both own it: both become ambiguous."""
    by_top: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["status"] in ("matched", "near") and r["suggestion"]:
            by_top.setdefault(r["suggestion"]["employee_id"], []).append(r)
    for group in by_top.values():
        if len(group) < 2:
            continue
        for r in group:
            others = ", ".join(_label(o) for o in group if o is not r)
            r["candidates"][0]["notes"].append("Cùng ứng viên với user SDP: " + others)
            r["status"], r["suggestion"] = "ambiguous", None
