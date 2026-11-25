"""Reconciliation by employee ID across SDP requests, SDP assets and BioStar.

Pure functions: given normalized snapshots, produce one row per employee with per-system cells
and the list of detected discrepancies. No I/O, so every rule is unit-tested directly.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from app.modules.lifecycle.parsing import fold
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


def _fmt(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "?"


def _latest(requests: list[dict[str, Any]]) -> dict[str, Any] | None:
    def key(r: dict[str, Any]) -> tuple[str, str]:
        return (r.get("effective_date") or "", r.get("created_at") or "")

    relevant = [r for r in requests if r.get("kind") and r.get("kind") != "ignore"]
    return max(relevant, key=key) if relevant else None


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
        elif r.get("parse_errors"):
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
            if a.get("state") and a["state"] != settings.asset_state_in_use:
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

    employees = set(req_by_emp) | set(users_by_emp) | set(bs_by_emp)
    rows: list[Row] = []
    for emp in sorted(employees):
        reqs = req_by_emp.get(emp, [])
        latest = _latest(reqs)
        users = users_by_emp.get(emp, [])
        bs = bs_by_emp.get(emp)
        ref_name = (
            (latest or {}).get("full_name")
            or (reqs[0].get("full_name") if reqs else None)
            or (users[0]["name"] if users else None)
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
            department=(latest or {}).get("department") or (users[0].get("department") if users else None),
            kind=(latest or {}).get("kind"),
            effective_date=date.fromisoformat(latest["effective_date"])
            if latest and latest.get("effective_date")
            else None,
            request_ids=[r["id"] for r in reqs],
            sdp_user_ids=owner_ids,
            asset_ids=[a["id"] for a in emp_assets],
            biostar_user_id=(bs.get("ref") or bs["user_id"]) if bs else None,
        )

        # --- cells -------------------------------------------------------
        # The kind/date is its own column in the UI; this cell says what SDP holds for the person.
        if reqs and latest is None:
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
                "label": str(len(in_use)) if emp_assets else "0",
                "detail": ", ".join(f"{a.get('name')} ({a.get('asset_tag') or '—'})" for a in emp_assets[:6])
                + (" · nối theo họ tên" if matched_by_name else ""),
            }
        if not sources.get("biostar"):
            row.cells["biostar"] = {"status": "skipped", "label": "Chưa đọc được", "detail": ""}
        elif bs is None:
            row.cells["biostar"] = {"status": "pending", "label": "Không có", "detail": ""}
        else:
            state = "Hoạt động" if bs["active"] else ("Vô hiệu" if bs["disabled"] else "Hết hạn")
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
                        f"Nghỉ việc từ {_fmt(today_eff)} nhưng user BioStar vẫn hoạt động.",
                    )
                )
            if sources.get("assets") and in_use:
                row.issues.append(
                    Issue(
                        "offboard_assets_in_use",
                        f"Nghỉ việc từ {_fmt(today_eff)} nhưng còn {len(in_use)} tài sản đang gán.",
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
                            f"Ngày hiệu lực {_fmt(today_eff)} nhưng chưa có user BioStar.",
                        )
                    )
                elif bs["cards"] + bs["fingerprints"] + bs["faces"] == 0:
                    row.issues.append(
                        Issue("onboard_no_credential", "User BioStar chưa có thẻ, vân tay hoặc khuôn mặt.")
                    )
            if sources.get("assets") and not emp_assets:
                row.issues.append(
                    Issue("onboard_no_assets", f"Ngày hiệu lực {_fmt(today_eff)} nhưng chưa có tài sản nào.")
                )
        if bs and sources.get("sdp") and sdp_has_emp_ids and not users and not reqs:
            row.issues.append(
                Issue(
                    "biostar_unknown_employee",
                    f"User BioStar “{bs['name']}” mang Mã NV {emp} nhưng không có user SDP hay request RTR.",
                )
            )
        if len(users) > 1:
            row.issues.append(
                Issue(
                    "sdp_duplicate_employee_id",
                    f"{len(users)} user SDP cùng Mã NV: " + ", ".join(u["name"] for u in users),
                )
            )
        names = {
            "BioStar": (bs or {}).get("name"),
            "Request": (latest or {}).get("full_name"),
            "SDP": users[0]["name"] if users else None,
        }
        present = {k: v for k, v in names.items() if v}
        if len({fold(v) for v in present.values()}) > 1:
            row.issues.append(Issue("name_mismatch", " · ".join(f"{k}: {v}" for k, v in present.items())))
        for r in reqs:
            if r.get("parse_errors"):
                row.issues.append(
                    Issue(
                        "request_parse_error",
                        f"Request #{r.get('display_id') or r['id']}: " + "; ".join(r["parse_errors"]),
                    )
                )
        rows.append(row)
    return ReconcileResult(rows=rows, notes=notes)
