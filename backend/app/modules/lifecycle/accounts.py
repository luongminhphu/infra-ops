"""Pure helpers for planning an AD / Microsoft 365 account (naming, OU, entitlements). No I/O."""

from __future__ import annotations

import re
from collections.abc import Callable

from app.modules.lifecycle.settings import LifecycleSettings, MatrixLevel, OuRule, fold


def _slug(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(word))


def name_parts(full_name: str) -> tuple[str, list[str]]:
    """(given, family+middle words), folded. "Nguyễn Thanh Bình" and "Bình. Nguyễn Thanh" both give
    ("binh", ["nguyen", "thanh"])."""
    raw = full_name.split()
    if not raw:
        return "", []
    if len(raw) > 1 and raw[0].endswith("."):
        given, rest = raw[0], raw[1:]
    else:
        given, rest = raw[-1], raw[:-1] if raw else []
    return _slug(given), [s for s in map(_slug, rest) if s]


def username_candidates(full_name: str, order: list[str], max_length: int = 20) -> list[str]:
    given, rest = name_parts(full_name)
    if not given:
        return []
    if rest:
        by_token = {
            "short": f"{given}.{''.join(w[0] for w in rest)}",
            "given_family": f"{given}.{rest[0]}",
            "given_full": f"{given}.{''.join(rest)}",
        }
        by_token["short2"], by_token["short3"] = by_token["short"] + "2", by_token["short"] + "3"
    else:
        by_token = {"short": given, "short2": given + "2", "short3": given + "3"}
    out = [by_token[t] for t in order if t in by_token and len(by_token[t]) <= max_length]
    return list(dict.fromkeys(out))


def pick_username(candidates: list[str], taken: Callable[[str], bool]) -> str | None:
    return next((c for c in candidates if not taken(c)), None)


def upn(sam: str, domain: str) -> str:
    if not domain:
        raise ValueError("Chưa cấu hình tên miền UPN.")
    return f"{sam}@{domain}".lower()


def _org(employee: dict) -> dict[str, str]:
    org = {k: fold(str(employee.get(k) or "")) for k in ("division", "department", "unit")}
    # "department" in job params is "Dept – Unit"; the rules match the department alone.
    org["department"] = fold(str(employee.get("department_only") or employee.get("department") or ""))
    return org


def matching_rules(employee: dict, settings: LifecycleSettings) -> list[OuRule]:
    """Every rule whose non-empty org fields all match, least specific first (ties: later rules first, so the
    earliest rule of the top score is last)."""
    org = _org(employee)
    hits: list[tuple[tuple[int, bool, bool, int], OuRule]] = []
    for i, r in enumerate(settings.ad_ou_map):
        want = {k: fold(getattr(r, k)) for k in org if getattr(r, k)}
        if want and all(org[k] == v for k, v in want.items()):
            hits.append(((len(want), "department" in want, "unit" in want, -i), r))
    return [r for _, r in sorted(hits, key=lambda h: h[0])]


def ou_for(employee: dict, settings: LifecycleSettings) -> tuple[str | None, str]:
    """(OU DN, why): the most specific matching rule that names an OU (division+department+unit >
    division+department > department > division), else the default OU, else None."""
    for r in reversed(matching_rules(employee, settings)):
        if r.ou:
            return r.ou, " / ".join(
                getattr(r, k) for k in ("division", "department", "unit") if getattr(r, k)
            )
    return (settings.ad_default_ou, "OU mặc định") if settings.ad_default_ou else (None, "")


def entitlements(level: MatrixLevel | None) -> dict[str, list[str]]:
    return {
        "license_skus": list(level.license_skus) if level else [],
        "license_names": list(level.license_names) if level else [],
        "software": list(level.software) if level else [],
    }
