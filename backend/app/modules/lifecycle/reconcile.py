"""Reconciliation by employee ID across SDP requests, SDP assets and BioStar.

Pure functions: given normalized snapshots, produce one row per employee with per-system cells
and the list of detected discrepancies. No I/O, so every rule is unit-tested directly.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from app.core.timeutil import TZ, fmt_date
from app.modules.lifecycle.normalize import EMPLOYEE_ID_DIGITS, id_key
from app.modules.lifecycle.parsing import fold, name_key
from app.modules.lifecycle.settings import LifecycleSettings

Severity = Literal["high", "medium", "low"]
SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}

RULES: dict[str, tuple[Severity, str, str]] = {
    # code: (severity, system, title)
    "offboard_biostar_active": ("high", "biostar", "Đã nghỉ việc nhưng user BioStar còn hoạt động"),
    "offboard_assets_in_use": ("high", "assets", "Đã nghỉ việc nhưng còn tài sản đang gán"),
    "onboard_no_biostar": ("medium", "biostar", "Đã onboard nhưng chưa có user BioStar"),
    "onboard_no_credential": ("low", "biostar", "User BioStar chưa đăng ký thẻ, vân tay hoặc khuôn mặt"),
    "onboard_no_assets": ("low", "assets", "Đã onboard nhưng chưa được gán tài sản"),
    "onboard_cancelled_biostar_active": (
        "high",
        "biostar",
        "Onboard đã huỷ nhưng user BioStar còn hoạt động",
    ),
    "onboard_cancelled_assets_in_use": ("medium", "assets", "Onboard đã huỷ nhưng còn tài sản đang gán"),
    "biostar_unknown_employee": ("medium", "biostar", "Mã NV trên BioStar không có trong SDP"),
    "sdp_duplicate_employee_id": ("medium", "sdp", "Mã NV trùng trên nhiều user SDP"),
    "name_mismatch": ("low", "biostar", "Họ tên không khớp giữa các hệ thống"),
    "request_parse_error": ("medium", "sdp", "Request RTR thiếu hoặc sai thông tin"),
    "asset_state_mismatch": ("low", "assets", "Trạng thái tài sản không khớp với việc gán"),
}


@dataclass
class Issue:
    rule: str
    message: str
    severity: Severity = "low"
    system: str = ""

    def __post_init__(self) -> None:
        sev, system, _ = RULES[self.rule]
        self.severity = sev
        self.system = system


@dataclass
class Row:
    employee_id: str
    full_name: str = ""
    department: str | None = None
    kind: str | None = None
    effective_date: date | None = None
    request_ids: list[str] = field(default_factory=list)
    latest_request_id: str | None = None  # the request that decides kind/date: the one a Job is for
    sdp_user_ids: list[str] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)
    biostar_user_id: str | None = None
    cells: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def max_severity(self) -> Severity | None:
        if not self.issues:
            return None
        return max((i.severity for i in self.issues), key=SEVERITY_ORDER.__getitem__)

    def to_dict(self) -> dict[str, Any]:
        return {
            "employee_id": self.employee_id,
            "full_name": self.full_name,
            "department": self.department,
            "kind": self.kind,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "request_ids": self.request_ids,
            "latest_request_id": self.latest_request_id,
            "sdp_user_ids": self.sdp_user_ids,
            "asset_ids": self.asset_ids,
            "biostar_user_id": self.biostar_user_id,
            "cells": self.cells,
            "issues": [i.__dict__ for i in self.issues],
            "max_severity": self.max_severity,
        }


@dataclass
class ReconcileResult:
    rows: list[Row]
    notes: list[dict[str, Any]]  # global findings not tied to one employee


def _latest(requests: list[dict[str, Any]]) -> dict[str, Any] | None:
    def key(r: dict[str, Any]) -> tuple[str, str]:
        return (r.get("effective_date") or "", r.get("created_at") or "")

    relevant = [r for r in requests if r.get("kind") and r.get("kind") != "ignore"]
    return max(relevant, key=key) if relevant else None


def _padded_id_key(emp: str) -> str:
    """``id_key`` that also treats "3551" and "003551" alike (BioStar often drops the leading zeros)."""
    return id_key(emp.zfill(EMPLOYEE_ID_DIGITS)) if emp.isdigit() else emp


def _cancelled_onboard_label(r: dict[str, Any]) -> str:
    created = None
    raw = str(r.get("created_at") or "")
    try:
        if len(raw) > 10:
            dt = datetime.fromisoformat(raw)
            created = (dt.astimezone(TZ) if dt.tzinfo else dt).date()
        else:
            created = date.fromisoformat(raw)
    except ValueError:
        pass
    return f"Request onboard #{r.get('display_id') or r['id']}" + (
        f" (tạo {fmt_date(created)})" if created else ""
    )


def reconcile(
    requests: list[dict[str, Any]],
    sdp_users: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    biostar_users: list[dict[str, Any]],
    settings: LifecycleSettings,
    today: date,
    sources: dict[str, bool] | None = None,
) -> ReconcileResult:
    """``sources`` tells which systems were read successfully; missing systems are not judged."""
    sources = sources or {"sdp": True, "assets": True, "biostar": True}
    notes: list[dict[str, Any]] = []

    req_by_emp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in requests:
        if r.get("employee_id"):
            req_by_emp[r["employee_id"]].append(r)
        elif r.get("parse_errors") and not settings.is_closed(r.get("status")):
            notes.append(
                {
                    "rule": "request_parse_error",
                    "request_id": r["id"],
                    "subject": r.get("subject"),
                    "message": "; ".join(r["parse_errors"]),
                }
            )

    users_by_emp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    users_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for u in sdp_users:
        if u.get("employee_id"):
            users_by_emp[u["employee_id"]].append(u)
        if u.get("name"):
            users_by_name[fold(u["name"])].append(u)
    sdp_has_emp_ids = bool(users_by_emp)
    if sources.get("sdp") and sdp_users and not sdp_has_emp_ids:
        notes.append(
            {
                "rule": "sdp_no_employee_ids",
                "message": f"Không user SDP nào có giá trị ở trường “{settings.sdp_user_employee_id_field}”. "
                "Tài sản sẽ được nối với nhân viên theo họ tên; kiểm tra mapping LDAP → SDP.",
            }
        )

    assets_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in assets:
        if a.get("user_id"):
            assets_by_user[a["user_id"]].append(a)
            # In repair / to be returned stay assigned on purpose.
            allowed = {
                settings.asset_state_in_use,
                settings.asset_state_in_repair,
                settings.asset_state_to_be_returned,
            }
            if a.get("state") and a["state"] not in allowed:
                notes.append(
                    {
                        "rule": "asset_state_mismatch",
                        "asset_id": a["id"],
                        "asset_tag": a.get("asset_tag"),
                        "message": f"Tài sản {a.get('name')} đang gán cho {a.get('user_name')} nhưng trạng thái là "
                        f"“{a['state']}” (cần “{settings.asset_state_in_use}”).",
                    }
                )
        elif a.get("state") == settings.asset_state_in_use:
            notes.append(
                {
                    "rule": "asset_state_mismatch",
                    "asset_id": a["id"],
                    "asset_tag": a.get("asset_tag"),
                    "message": f"Tài sản {a.get('name')} ở trạng thái “{a['state']}” nhưng chưa gán cho ai.",
                }
            )

    bs_by_emp = {b.get("employee_id") or b["user_id"]: b for b in biostar_users if b.get("user_id")}

    # Cancelled onboard only (no live request): the person may still hold access granted before the cancellation.
    # Names must match so a mistyped ID never accuses someone else.
    cancelled_ob: dict[str, dict[str, Any]] = {}
    cancelled_bs: dict[str, dict[str, Any]] = {}
    for emp, reqs in req_by_emp.items():
        if any(not settings.is_cancelled(r.get("status")) for r in reqs):
            continue
        ob = _latest([r for r in reqs if r.get("kind") == "onboard"])
        if not (ob and ob.get("full_name")):
            continue
        cancelled_ob[emp] = ob
        ob_name = name_key(ob["full_name"])

        def _ok(b: dict[str, Any] | None, want: str = ob_name) -> bool:
            return bool(
                b and b["active"] and not b.get("retired") and b.get("name") and name_key(b["name"]) == want
            )

        if _ok(bs_by_emp.get(emp)):
            cancelled_bs[emp] = bs_by_emp[emp]
            continue
        # shortcut: unclaimed BioStar user with the same trailing ID digits; only used when unique.
        cand = [
            b
            for k, b in bs_by_emp.items()
            if k not in req_by_emp
            and k not in users_by_emp
            and _padded_id_key(k) == _padded_id_key(emp)
            and _ok(b)
        ]
        if len(cand) == 1:
            cancelled_bs[emp] = cand[0]
    used_bs = {b["user_id"] for b in cancelled_bs.values()}

    # SDP is the source of truth: employees are SDP users and RTR requests; BioStar only adds access data. BioStar
    # users matching neither are listed apart (access review), never as employees – unless SDP could not be read.
    # People moved to the "left the company" BioStar group are hidden unless an RTR request still concerns them.
    retired = {e for e, b in bs_by_emp.items() if b.get("retired")}
    sdp_known = sources.get("sdp") and sdp_has_emp_ids
    employees = set(req_by_emp) | (set(users_by_emp) - retired)
    if not sdp_known:
        employees |= set(bs_by_emp) - retired
    elif sources.get("biostar"):
        for emp, b in sorted(bs_by_emp.items(), key=lambda kv: (not kv[1]["active"], kv[0])):
            if (
                emp not in employees
                and emp not in retired
                and emp not in users_by_emp
                and b["user_id"] not in used_bs
            ):
                notes.append(
                    {
                        "rule": "biostar_unknown_employee",
                        "employee_id": emp,
                        "name": b.get("name"),
                        "active": b["active"],
                        "group": b.get("group"),
                        "source": b.get("source"),
                        "message": f"User BioStar “{b.get('name')}” mang Mã NV {emp} nhưng không có user SDP hay "
                        "request RTR"
                        + (" – vẫn còn quyền ra vào." if b["active"] else " (đã khoá / hết hạn)."),
                    }
                )
    rows: list[Row] = []
    for emp in sorted(employees):
        reqs = req_by_emp.get(emp, [])
        live = [r for r in reqs if not settings.is_cancelled(r.get("status"))]
        latest = _latest(live)
        users = users_by_emp.get(emp, [])
        bs = bs_by_emp.get(emp)
        # SDP is the reference for the person's name; RTR and BioStar only fill in when SDP has no user.
        ref_name = (
            (users[0]["name"] if users else None)
            or (latest or {}).get("full_name")
            or (live[0].get("full_name") if live else None)
            or (cancelled_ob.get(emp) or {}).get("full_name")
            or (bs or {}).get("name")
            or ""
        )

        # Asset owner: SDP user with this employee ID, else (optional) a unique name match.
        owner_ids = [u["id"] for u in users]
        matched_by_name = False
        if not owner_ids and settings.match_assets_by_name and ref_name:
            same = users_by_name.get(fold(ref_name), [])
            if len(same) == 1:
                owner_ids = [same[0]["id"]]
                matched_by_name = True
        emp_assets = [a for uid in owner_ids for a in assets_by_user.get(uid, [])]
        in_use = [a for a in emp_assets if a.get("state") == settings.asset_state_in_use]

        row = Row(
            employee_id=emp,
            full_name=ref_name,
            department=(users[0].get("department") if users else None) or (latest or {}).get("department"),
            kind=(latest or {}).get("kind"),
            effective_date=date.fromisoformat(latest["effective_date"])
            if latest and latest.get("effective_date")
            else None,
            request_ids=[r["id"] for r in reqs],
            latest_request_id=latest["id"] if latest else None,
            sdp_user_ids=owner_ids,
            asset_ids=[a["id"] for a in emp_assets],
            biostar_user_id=(bs.get("ref") or bs["user_id"]) if bs else None,
        )

        # --- cells -------------------------------------------------------
        # The kind/date is its own column in the UI; this cell says what SDP holds for the person.
        if reqs and not live:
            sdp_label = "RTR đã huỷ"
        elif reqs and latest is None:
            sdp_label = "RTR chưa phân loại"
        elif reqs and users:
            sdp_label = "RTR + user"
        elif reqs:
            sdp_label = "RTR"
        elif users:
            sdp_label = "User SDP"
        else:
            sdp_label = "Không có"
        row.cells["sdp"] = {
            "status": "match" if (reqs or users) else "pending",
            "label": sdp_label,
            "detail": f"{len(reqs)} request RTR · {len(users)} user SDP",
        }
        if not sources.get("assets"):
            row.cells["assets"] = {"status": "skipped", "label": "Chưa đọc được", "detail": ""}
        else:
            row.cells["assets"] = {
                "status": "match" if emp_assets else "pending",
                "label": f"{len(in_use) if emp_assets else 0} tài sản",
                "detail": ", ".join(f"{a.get('name')} ({a.get('asset_tag') or '—'})" for a in emp_assets[:6])
                + (" · nối theo họ tên" if matched_by_name else ""),
            }
        if not sources.get("biostar"):
            row.cells["biostar"] = {"status": "skipped", "label": "Chưa đọc được", "detail": ""}
        elif bs is None:
            row.cells["biostar"] = {"status": "pending", "label": "Không có", "detail": ""}
        else:
            state = (
                "Nhóm loại trừ"
                if bs.get("retired")
                else ("Hoạt động" if bs["active"] else ("Vô hiệu" if bs["disabled"] else "Hết hạn"))
            )
            row.cells["biostar"] = {
                "status": "match" if bs["active"] else "skipped",
                "label": state,
                "detail": (f"{bs['source']} (user {bs['user_id']}) · " if bs.get("source") else "")
                + f"Nhóm: {bs.get('group') or '—'} · thẻ {bs['cards']} · vân tay {bs['fingerprints']} · "
                f"khuôn mặt {bs['faces']}",
            }

        # --- rules -------------------------------------------------------
        today_eff = row.effective_date
        if (
            row.kind == "offboard"
            and today_eff
            and today_eff + timedelta(days=settings.offboard_grace_days) <= today
        ):
            if sources.get("biostar") and bs and bs["active"]:
                row.issues.append(
                    Issue(
                        "offboard_biostar_active",
                        f"Nghỉ việc từ {fmt_date(today_eff, '?')} nhưng user BioStar vẫn hoạt động.",
                    )
                )
            if sources.get("assets") and in_use:
                row.issues.append(
                    Issue(
                        "offboard_assets_in_use",
                        f"Nghỉ việc từ {fmt_date(today_eff, '?')} nhưng còn {len(in_use)} tài sản đang gán.",
                    )
                )
        if (
            row.kind == "onboard"
            and today_eff
            and today_eff + timedelta(days=settings.onboard_grace_days) <= today
        ):
            if sources.get("biostar"):
                if bs is None:
                    row.issues.append(
                        Issue(
                            "onboard_no_biostar",
                            f"Ngày hiệu lực {fmt_date(today_eff, '?')} nhưng chưa có user BioStar.",
                        )
                    )
                elif bs["cards"] + bs["fingerprints"] + bs["faces"] == 0:
                    row.issues.append(
                        Issue("onboard_no_credential", "User BioStar chưa có thẻ, vân tay hoặc khuôn mặt.")
                    )
            if sources.get("assets") and not emp_assets:
                row.issues.append(
                    Issue(
                        "onboard_no_assets",
                        f"Ngày hiệu lực {fmt_date(today_eff, '?')} nhưng chưa có tài sản nào.",
                    )
                )
        ob = cancelled_ob.get(emp)
        if ob:
            ob_bs = cancelled_bs.get(emp)
            if sources.get("biostar") and ob_bs:
                row.issues.append(
                    Issue(
                        "onboard_cancelled_biostar_active",
                        f"{_cancelled_onboard_label(ob)} đã huỷ nhưng user BioStar {ob_bs['user_id']} vẫn hoạt động"
                        " – khoá hoặc xoá user trên BioStar.",
                    )
                )
            if (
                sources.get("assets")
                and in_use
                and len(users) == 1
                and name_key(users[0].get("name") or "") == name_key(ob["full_name"])
            ):
                row.issues.append(
                    Issue(
                        "onboard_cancelled_assets_in_use",
                        f"{_cancelled_onboard_label(ob)} đã huỷ nhưng còn {len(in_use)} tài sản đang gán"
                        " – thu hồi trên SDP.",
                    )
                )
        if len(users) > 1:
            row.issues.append(
                Issue(
                    "sdp_duplicate_employee_id",
                    f"{len(users)} user SDP cùng Mã NV: "
                    + ", ".join(
                        f"{u['name']} (id {u['id']}{', ' + u['email'] if u.get('email') else ''})"
                        for u in users
                    ),
                )
            )
        names = {
            "BioStar": (bs or {}).get("name"),
            "Request": (latest or {}).get("full_name"),
            "SDP": users[0]["name"] if users else None,
        }
        present = {k: v for k, v in names.items() if v}
        if len({name_key(v) for v in present.values()}) > 1:
            row.issues.append(Issue("name_mismatch", " · ".join(f"{k}: {v}" for k, v in present.items())))
        for r in reqs:
            if r.get("parse_errors") and not settings.is_closed(r.get("status")):
                row.issues.append(
                    Issue(
                        "request_parse_error",
                        f"Request #{r.get('display_id') or r['id']}: " + "; ".join(r["parse_errors"]),
                    )
                )
        rows.append(row)
    return ReconcileResult(rows=rows, notes=notes)
