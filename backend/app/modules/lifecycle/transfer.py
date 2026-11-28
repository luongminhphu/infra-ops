"""Pure planning for a transfer (thuyên chuyển): what changes for one person. No I/O.

An absent / empty new value always means "unchanged": nothing is added, removed or written for it."""

from __future__ import annotations

from typing import Any

from app.core.connectors.active_directory import inside
from app.modules.lifecycle.accounts import _org, matching_rules
from app.modules.lifecycle.settings import AllocationMatrix, LifecycleSettings, OuRule, fold

__all__ = [
    "FIELDS",
    "attribute_changes",
    "biostar_groups",
    "department_text",
    "full_org",
    "group_changes",
    "incomplete",
    "license_changes",
    "managed_biostar",
    "managed_groups",
    "managed_skus",
    "matching_rules",
    "org_employee",
    "target_groups",
    "target_ou",
]

TITLE_MAX = 64
FIELDS = ("division", "department", "unit")


def _parts(e: dict) -> dict[str, str]:
    dept, unit = str(e.get("department_only") or "").strip(), str(e.get("unit") or "").strip()
    if not dept:
        head, _, tail = str(e.get("department") or "").partition(" – ")
        dept, unit = head.strip(), unit or tail.strip()
    return {"division": str(e.get("division") or "").strip(), "department": dept, "unit": unit}


def full_org(
    request: dict, old: dict, ad_department: str, settings: LifecycleSettings | None = None
) -> tuple[dict[str, str], frozenset[str], str] | None:
    """The whole org after the transfer: (org, known fields, source). A field the request leaves out is borrowed from
    the old request, else AD's "Phòng – Bộ phận"; a unit is known-empty when the department changes. A field
    nobody knows stays out of `known`: nothing is judged on it. A borrowed value no rule names (e.g. AD's
    "Kỹ thuật - Tổ 1") is not known either (C-20). None = the request gives no org at all."""
    req = _parts(request)
    if not any(req.values()):
        return None
    base, src = _parts(old), "request_old"
    if not base["department"]:
        base, src = _parts({"department": ad_department}), "ad"
    org = {k: v for k, v in req.items() if v}
    if "department" not in org and base["department"]:
        org["department"] = base["department"]
    if "unit" not in org:
        if req["department"] and base["department"] and fold(req["department"]) != fold(base["department"]):
            org["unit"] = ""
        elif base["department"]:
            org["unit"] = base["unit"]
    if "division" not in org and base["division"]:
        org["division"] = base["division"]
    known = {
        k
        for k, v in org.items()
        if req[k]
        or not v
        or settings is None
        or any(fold(getattr(r, k)) == fold(v) for r in settings.ad_ou_map)
    }
    return org, frozenset(known), ("request" if all(req.get(k) for k in org) else src)


def org_employee(org: dict[str, str]) -> dict[str, str]:
    return {
        "division": org.get("division", ""),
        "department_only": org.get("department", ""),
        "unit": org.get("unit", ""),
    }


def department_text(org: dict[str, str]) -> str:
    """AD `department` value, like onboard: "Phòng – Bộ phận" (empty when the department is unknown)."""
    d = org.get("department", "")
    return f"{d} – {org['unit']}" if d and org.get("unit") else d


def _status(rule: OuRule, employee: dict, known: frozenset[str] | None) -> str:
    """ "match" / "no" / "unknown" (depends on a field nobody knows) for one rule."""
    want = {k for k in FIELDS if getattr(rule, k)}
    if not want:
        return "no"
    org, seen = _org(employee), set(FIELDS) if known is None else known
    if any(fold(getattr(rule, k)) != org[k] for k in want & seen):
        return "no"
    return "match" if want <= seen else "unknown"


def incomplete(employee: dict, settings: LifecycleSettings, known: frozenset[str] | None) -> bool:
    return any(_status(r, employee, known) == "unknown" for r in settings.ad_ou_map)


def _layer(
    employee: dict, settings: LifecycleSettings, known: frozenset[str] | None, attr: str
) -> tuple[list[str], list[str], list[str]]:
    """(wanted, judged, protected) values of one rule attribute: only values of rules we can decide are ever
    removed; values of undecidable rules are protected."""
    rules = settings.ad_ou_map
    wanted = [v for r in matching_rules(employee, settings) for v in getattr(r, attr)]
    judged = [v for r in rules if _status(r, employee, known) != "unknown" for v in getattr(r, attr)]
    protected = [v for r in rules if _status(r, employee, known) == "unknown" for v in getattr(r, attr)]
    return wanted, judged, protected


def _org_given(employee: dict) -> bool:
    return any(employee.get(k) for k in ("division", "department", "department_only", "unit"))


def same_dn(a: str, b: str) -> bool:
    # Unparsable DNs fall back to a case-insensitive text compare.
    return (inside(a, b) and inside(b, a)) or a.strip().casefold() == b.strip().casefold()


def _dedupe_dns(dns: list[str]) -> list[str]:
    out: list[str] = []
    for d in dns:
        if not any(same_dn(d, o) for o in out):
            out.append(d)
    return out


def target_ou(employee: dict, settings: LifecycleSettings, known: frozenset[str] | None = None) -> str | None:
    """OU of the most specific matching rule that names one. Never the default OU; none when an undecidable rule
    names an OU (we cannot tell which one applies)."""
    if not _org_given(employee) or any(
        r.ou and _status(r, employee, known) == "unknown" for r in settings.ad_ou_map
    ):
        return None
    return next((r.ou for r in reversed(matching_rules(employee, settings)) if r.ou), None)


def managed_groups(settings: LifecycleSettings) -> list[str]:
    return _dedupe_dns([g for r in settings.ad_ou_map for g in r.groups])


def target_groups(employee: dict, settings: LifecycleSettings) -> list[str] | None:
    if not _org_given(employee):
        return None
    return _dedupe_dns([g for r in matching_rules(employee, settings) for g in r.groups])


def group_changes(
    current_member_of: list[str],
    employee: dict,
    settings: LifecycleSettings,
    known: frozenset[str] | None = None,
) -> dict[str, list[str]]:
    """Add what the new org needs; remove only groups of decidable rules the new org does not need."""
    if not _org_given(employee):
        return {"add": [], "remove": []}
    wanted, judged, protected = _layer(employee, settings, known, "groups")
    target = _dedupe_dns(wanted)
    has = lambda dn, dns: any(same_dn(dn, d) for d in dns)  # noqa: E731
    add = [g for g in target if not has(g, current_member_of)]
    remove = [
        c
        for c in _dedupe_dns(current_member_of)
        if has(c, judged) and not has(c, protected) and not has(c, target)
    ]
    return {"add": add, "remove": remove}


def managed_skus(matrix: AllocationMatrix | None) -> set[str]:
    return {s.lower() for lv in matrix.levels for s in lv.license_skus} if matrix else set()


def managed_biostar(settings: LifecycleSettings) -> set[str]:
    return {str(i) for i in settings.biostar_access_group_ids} | {
        str(i) for r in settings.ad_ou_map for i in r.biostar_access_group_ids
    }


def license_changes(
    current_skus: list[str],
    new_level: Any,
    matrix: AllocationMatrix | None,
    direct_skus: list[str] | None = None,
) -> dict[str, list[str]]:
    """Swap to the new row's SKUs; only matrix SKUs assigned directly (not by a group) are ever removed
    (`direct_skus` None = all of `current_skus`). No new SKUs = no change."""
    target = [s.lower() for s in (getattr(new_level, "license_skus", None) or [])]
    if not target or matrix is None:
        return {"add": [], "remove": []}
    managed = managed_skus(matrix)
    have = [s.lower() for s in current_skus]
    direct = set(have) if direct_skus is None else {s.lower() for s in direct_skus}
    return {
        "add": [s for s in target if s not in have],
        "remove": [s for s in have if s in managed and s in direct and s not in target],
    }


def biostar_groups(
    current_ids: list[str],
    employee: dict,
    settings: LifecycleSettings,
    known: frozenset[str] | None = None,
) -> tuple[list[str], bool]:
    """(new access-group id list, changed). Ids no decidable rule manages are kept."""
    current = [str(i) for i in current_ids]
    if not _org_given(employee):
        return current, False
    wanted, judged, protected = _layer(employee, settings, known, "biostar_access_group_ids")
    target = list(dict.fromkeys(str(i) for i in [*settings.biostar_access_group_ids, *wanted]))
    drop = {str(i) for i in judged if i not in protected} | {
        str(i) for i in settings.biostar_access_group_ids
    }
    kept = [i for i in current if i not in drop or i in target]
    new = list(dict.fromkeys([*kept, *target]))
    return new, set(new) != set(current)


def attribute_changes(current_ad: dict, employee: dict) -> dict[str, str]:
    """Attributes to write: only non-empty new values that differ from AD."""
    want: dict[str, str] = {}
    if title := (employee.get("job_title") or "").strip():
        want["title"] = title[:TITLE_MAX]
    if dept := (employee.get("department") or "").strip():  # "Phòng – Bộ phận", like onboard
        want["department"] = dept[:TITLE_MAX]
    if mid := str(employee.get("manager_employee_id") or "").strip():
        want["manager_employee_id"] = mid
    return {k: v for k, v in want.items() if str(current_ad.get(k) or "") != v}
