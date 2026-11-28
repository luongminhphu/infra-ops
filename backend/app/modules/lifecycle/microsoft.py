"""Microsoft steps of the lifecycle workflows (AD / Exchange / Graph with the service credentials).

Never put a password anywhere: the AD client sets a random one that is not returned.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ldap3.utils.dn import escape_rdn

from app.core.connections.service import open_connection
from app.core.connectors.active_directory import AdClient, inside
from app.core.connectors.base import Purpose
from app.core.connectors.biostar import BioStarClient
from app.core.connectors.exchange import ExchangeClient, ExchangeNotFound
from app.core.connectors.http import ConnectorHttpError
from app.core.connectors.msgraph import GraphClient
from app.core.errors import AppError
from app.core.modules.registry import get_registry
from app.core.rbac.service import permissions_for_user
from app.core.timeutil import today_vn
from app.core.workflow import engine
from app.core.workflow.definitions import StepContext, StepOutcome
from app.modules.lifecycle import accounts
from app.modules.lifecycle import transfer as tr
from app.modules.lifecycle.parsing import name_key
from app.modules.lifecycle.settings import LifecycleSettings, main_biostar
from app.modules.lifecycle.settings import load as load_module_settings

DISABLED = "Tích hợp Microsoft đang tắt."
UAC_NORMAL, UAC_DISABLED = 512, 514
CUSTOM_SAM = re.compile(r"^[a-z0-9][a-z0-9.\-_]*$")
ASSIGN = "lifecycle.job.assign"
DEPARTMENT_MAX = 64
MAILBOX_NOT_FOUND_WAIT = 600  # Enable-RemoteMailbox "not found" = AD replication lag: retry up to 10 minutes
EMPLOYEE_ID = re.compile(r"^\d+$")
BAD_ID = "Mã NV không hợp lệ – sửa request RTR rồi tạo Job lại."


def ms_enabled(settings: LifecycleSettings) -> bool:
    return settings.microsoft_enabled


@asynccontextmanager
async def ad(ctx: StepContext, purpose: Purpose) -> AsyncIterator[AdClient]:
    s = await load_module_settings(ctx.session)
    _, conn = await open_connection(ctx.session, "active_directory", purpose, profile_id=s.ad_profile_id)
    async with AdClient(conn) as c:
        yield c


@asynccontextmanager
async def exchange(ctx: StepContext, purpose: Purpose) -> AsyncIterator[ExchangeClient]:
    s = await load_module_settings(ctx.session)
    _, conn = await open_connection(ctx.session, "exchange", purpose, profile_id=s.exchange_profile_id)
    async with ExchangeClient(conn) as c:
        yield c


@asynccontextmanager
async def graph(ctx: StepContext, purpose: Purpose) -> AsyncIterator[GraphClient]:
    s = await load_module_settings(ctx.session)
    _, conn = await open_connection(ctx.session, "msgraph", purpose, profile_id=s.graph_profile_id)
    async with GraphClient(conn) as c:
        yield c


def _err(exc: Exception) -> str:
    if isinstance(exc, ConnectorHttpError):
        return f"{exc.message} {exc.hint}".strip()
    return getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"


def _split_name(full_name: str) -> tuple[str, str]:
    """(given, surname) keeping accents; same convention as accounts.name_parts."""
    w = full_name.split()
    if not w:
        return "", ""
    if len(w) > 1 and w[0].endswith("."):
        return w[0].rstrip("."), " ".join(w[1:])
    return w[-1], " ".join(w[:-1])


def _same_dn(a: str, b: str) -> bool:
    return inside(a, b) and inside(b, a)


def _waited(ctx: StepContext) -> float:
    since = getattr(ctx.step, "waiting_since", None)
    return (datetime.now(UTC) - since).total_seconds() if since else 0.0


async def _can_assign(ctx: StepContext) -> bool:
    uid = getattr(ctx.actor, "user_id", None)
    return uid is not None and ASSIGN in await permissions_for_user(ctx.session, uid, get_registry())


def _emp(ctx: StepContext) -> dict[str, Any]:
    return ctx.params.get("employee") or {}


# ------------------------------------------------------------------ account_plan
async def prepare_account_plan(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("skipped", DISABLED)  # the engine must finish a manual step skipped in prepare
    emp = _emp(ctx)
    eid = str(emp.get("id") or "")
    warnings: list[str] = []
    valid = bool(EMPLOYEE_ID.match(eid))
    if not valid:
        warnings.append(BAD_ID)
    level = None
    if s.allocation_matrix is not None:
        level, _ = s.allocation_matrix.level_for(emp.get("job_title"), None, emp.get("department_only"))
    ent = accounts.entitlements(level)

    full = emp.get("full_name") or ""
    given, surname = _split_name(full)
    names = accounts.username_candidates(full, s.naming_order, s.sam_max_length)
    if not names:
        warnings.append("Không tạo được username từ họ tên.")
    cands = [
        {"sam": n, "upn": accounts.upn(n, s.upn_domain) if s.upn_domain else "", "free": True} for n in names
    ]
    if not s.upn_domain:
        warnings.append("Chưa cấu hình tên miền UPN.")
    ou, ou_rule = accounts.ou_for(
        {"division": emp.get("division"), "department": emp.get("department_only"), "unit": emp.get("unit")},
        s,
    )
    if not ou:
        warnings.append("Chưa xác định được OU – chọn OU thủ công.")
    elif s.ad_base_ou and not inside(ou, s.ad_base_ou):
        warnings.append(f"OU {ou} nằm ngoài OU gốc {s.ad_base_ou} – chọn OU khác.")
        ou = ""
    manager_dn = None
    cn = full
    dept = emp.get("department") or ""
    if len(dept) > DEPARTMENT_MAX:
        warnings.append(f"Phòng ban dài hơn {DEPARTMENT_MAX} ký tự – sẽ bị cắt khi ghi vào AD.")
        dept = dept[:DEPARTMENT_MAX]
    try:
        async with ad(ctx, Purpose.READ) as c:
            for cand in cands:
                cand["free"] = not await c.exists_any(cand["sam"], cand["upn"] or None, None)
            if ou and full and await c.get(f"CN={escape_rdn(full)},{ou}"):
                cn = f"{full} ({eid})"
                warnings.append(f"Đã có đối tượng CN={full} trong OU – dùng CN {cn}.")
            if valid:
                for u in await c.find_users(eid):
                    warnings.append(
                        f"Mã NV {eid} đã có tài khoản {u.get('sAMAccountName')} ({u['dn']}) – "
                        "tài khoản cũ không bao giờ được tự bật lại, IT xử lý thủ công."
                    )
            mid = str(emp.get("manager_employee_id") or "")
            if EMPLOYEE_ID.match(mid):
                found = await c.find_users(mid)
                if len(found) == 1:
                    manager_dn = found[0]["dn"]
            if not manager_dn:
                warnings.append("Không xác định được quản lý trực tiếp trên AD – bỏ qua thuộc tính manager.")
    except Exception as exc:
        warnings.append(f"Không kiểm tra được AD ({_err(exc)}) – username sẽ được kiểm tra lại ở bước tạo.")
    if cands and s.upn_domain:
        try:
            async with graph(ctx, Purpose.READ) as g:
                for cand in cands:
                    if cand["free"] and await g.address_taken(cand["upn"]):
                        cand["free"] = False
        except Exception as exc:
            warnings.append(
                f"Không kiểm tra được Entra ({_err(exc)}) – UPN sẽ không được kiểm tra trùng trên Entra."
            )
    free = next((c for c in cands if c["free"]), None)
    if cands and free is None:
        warnings.append("Mọi username đề xuất đã bị dùng – nhập username khác.")
    out = {
        "employee_id": eid,
        "employee_id_valid": valid,
        "display_name": full,
        "cn": cn,
        "given_name": given,
        "surname": surname,
        "title": emp.get("job_title") or "",
        "department": dept,
        "phone": emp.get("phone") or "",
        "level": level.name if level else None,
        **ent,
        "candidates": cands,
        "proposed_sam": free["sam"] if free else None,
        "sam_max_length": s.sam_max_length,
        "ou": ou or "",
        "ou_rule": ou_rule,
        # Choices for people allowed to pick another OU (configured OUs, never Ban_Users) – same rule as the run check.
        "ou_options": [
            o
            for o in dict.fromkeys([s.ad_default_ou, *(r.ou for r in s.ad_ou_map)])
            if o and not (s.ad_ban_ou and inside(o, s.ad_ban_ou))
        ],
        "manager_dn": manager_dn,
        "warnings": warnings,
    }
    return StepOutcome("waiting", f"Đề xuất tài khoản: {out['proposed_sam'] or 'chưa có'}.", out)


async def run_account_plan(ctx: StepContext) -> StepOutcome:
    """Manual completion (API request, no external calls): pin the chosen username / OU."""
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("done", DISABLED)
    plan = dict(ctx.step.output or {})
    if "candidates" not in plan:
        raise AppError(
            "Chưa có dữ liệu đề xuất tài khoản (bước chuẩn bị chưa chạy xong).", code="plan_not_ready"
        )
    if not plan.get("employee_id_valid"):
        raise AppError(BAD_ID, code="invalid_employee_id")
    sam = str(ctx.input.get("sam") or "").strip().lower()
    cand = next((c for c in plan["candidates"] if c["sam"] == sam), None)
    custom = cand is None
    if custom and not (CUSTOM_SAM.match(sam) and not sam.endswith(".") and len(sam) <= s.sam_max_length):
        raise AppError(
            f"Username không hợp lệ (chữ thường/số/.-_, không kết thúc bằng dấu chấm, tối đa {s.sam_max_length} ký tự).",
            code="invalid_sam",
        )
    if cand is not None and not cand["free"]:
        raise AppError(f"Username {sam} đã được dùng trên AD.", code="sam_taken")
    if not s.upn_domain:
        raise AppError("Chưa cấu hình tên miền UPN.", code="upn_domain_missing")
    ou = str(ctx.input.get("ou") or plan.get("ou") or "").strip()
    if not ou:
        raise AppError("Chọn OU cho tài khoản.", code="ou_missing")
    other_ou = not _same_dn(ou, str(plan.get("ou") or ""))
    if other_ou:
        allowed = [s.ad_default_ou, *(r.ou for r in s.ad_ou_map)]
        if s.ad_ban_ou and inside(ou, s.ad_ban_ou):
            raise AppError("Không tạo tài khoản mới trong OU Ban_Users.", code="ou_ban")
        if not any(a and _same_dn(ou, a) for a in allowed):
            raise AppError("OU không nằm trong danh sách OU đã cấu hình.", code="ou_not_configured")
    if (custom or other_ou) and not await _can_assign(ctx):
        raise AppError(
            "Chỉ người có quyền phân công mới được nhập username/OU khác đề xuất.", code="forbidden"
        )
    note = " (tuỳ chọn – kiểm tra trùng ở bước tạo)" if custom else ""
    pinned = {**plan, "sam": sam, "upn": accounts.upn(sam, s.upn_domain), "ou": ou, "custom": custom}
    return StepOutcome("done", f"Đã chốt username {sam}{note}.", pinned)


# ------------------------------------------------------------------ ad_account
def _pinned(ctx: StepContext) -> dict[str, Any]:
    plan = ctx.outputs.get("account_plan") or {}
    if not plan.get("sam") or not plan.get("ou"):
        raise AppError("Chưa có kế hoạch tài khoản đã chốt.", code="plan_missing")
    return plan


async def preview_ad_account(ctx: StepContext) -> list[str]:
    p = ctx.outputs.get("account_plan") or {}
    return [
        f"Tạo tài khoản AD {p.get('sam')} ({p.get('upn')}) trong OU {p.get('ou')}.",
        f"employeeID = {p.get('employee_id')}; tài khoản được bật sau khi đặt mật khẩu ngẫu nhiên (đổi khi đăng nhập lần đầu).",
        "Bật remote mailbox trên Exchange.",
    ]


async def ad_account(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("skipped", DISABLED)
    p = _pinned(ctx)
    sam, upn, ou, eid = p["sam"], p["upn"], p["ou"], p["employee_id"]
    if not EMPLOYEE_ID.match(str(eid)):
        return StepOutcome("failed", BAD_ID)
    if not inside(ou, s.ad_base_ou) or (s.ad_ban_ou and inside(ou, s.ad_ban_ou)):
        return StepOutcome(
            "failed", f"OU {ou} không hợp lệ (ngoài OU gốc hoặc là Ban_Users) – chốt lại kế hoạch."
        )
    before: dict[str, Any] = {"existing": False, "dn": None, "uac": None, "remote_mailbox": False}
    try:
        async with ad(ctx, Purpose.WRITE) as c:
            users = await c.find_users(eid)
            if len(users) > 1:
                return StepOutcome(
                    "failed", f"Mã NV {eid} có {len(users)} tài khoản trên AD – xử lý thủ công."
                )
            if users and str(users[0].get("sAMAccountName") or "").lower() != sam:
                return StepOutcome(
                    "failed",
                    f"Mã NV {eid} đã có tài khoản {users[0].get('sAMAccountName')} – kiểm tra trên AD.",
                )
            if users:
                dn = users[0]["dn"]
                if (
                    not _same_dn(dn, f"CN={escape_rdn(p.get('cn') or p['display_name'])},{ou}")
                    or int(users[0].get("adminCount") or 0) == 1
                ):
                    return StepOutcome(
                        "failed",
                        f"Mã NV {eid} đã có tài khoản {users[0].get('sAMAccountName')} tại {dn}, "
                        "không phải tài khoản của kế hoạch – IT xử lý thủ công (không tự bật lại tài khoản cũ).",
                    )
                uac = int(users[0].get("userAccountControl") or 0)
                before.update(existing=True, dn=dn, uac=uac)
                if uac & 0x2:  # a previous attempt stopped before enabling
                    await c.set_password_random(dn)
                    await c.modify(dn, {"pwdLastSet": 0})
                    await c.enable(dn)
            else:
                if await c.exists_any(sam, upn, upn):
                    return StepOutcome(
                        "failed", "Username đã bị dùng – huỷ Job và tạo lại, hoặc sửa trên AD."
                    )
                try:
                    async with graph(ctx, Purpose.READ) as g:
                        taken = await g.address_taken(upn)
                except AppError:
                    raise
                except Exception as exc:
                    return StepOutcome(
                        "waiting", f"Chưa kiểm tra được Entra ({_err(exc)}) – thử lại.", retry_in_seconds=300
                    )
                if taken:
                    return StepOutcome("failed", f"UPN {upn} đã tồn tại trên Entra ID – huỷ Job và tạo lại.")
                attrs: dict[str, Any] = {
                    "sAMAccountName": sam,
                    "userPrincipalName": upn,
                    "givenName": p["given_name"],
                    "sn": p["surname"],
                    "displayName": p["display_name"],
                    "employeeID": str(eid),
                    "title": p["title"],
                    "department": str(p["department"])[:DEPARTMENT_MAX],
                    "userAccountControl": UAC_DISABLED,
                }
                if p.get("phone"):
                    attrs["telephoneNumber"] = p["phone"]
                if p.get("manager_dn"):
                    attrs["manager"] = p["manager_dn"]
                cn = p.get("cn") or p["display_name"]
                if await c.get(f"CN={escape_rdn(cn)},{ou}"):
                    return StepOutcome(
                        "failed", f"Đã có đối tượng CN={cn} trong OU {ou} – chọn OU khác hoặc sửa trên AD."
                    )
                dn = await c.add_user(ou, cn, {k: v for k, v in attrs.items() if v})
                await c.set_password_random(dn)
                await c.modify(dn, {"pwdLastSet": 0})
                await c.enable(dn)
        async with exchange(ctx, Purpose.WRITE) as x:
            if await x.get_remote_mailbox(upn) is None:
                routing = f"{sam}@{s.remote_routing_domain}" if s.remote_routing_domain else None
                try:
                    await x.enable_remote_mailbox(dn, routing)  # always the DN, never sAM/alias
                except ExchangeNotFound:
                    if _waited(ctx) < MAILBOX_NOT_FOUND_WAIT:
                        return StepOutcome(
                            "waiting",
                            "Exchange chưa thấy tài khoản (chờ đồng bộ AD) – thử lại sau 60 giây.",
                            before_state=before,
                            retry_in_seconds=60,
                        )
                    raise
            else:
                before["remote_mailbox"] = True
    except AppError:
        raise
    except Exception as exc:
        return StepOutcome("failed", f"Không hoàn tất tạo tài khoản: {_err(exc)}", before_state=before)
    out = {"dn": dn, "sam": sam, "upn": upn, "employee_id": eid, "mailbox_enabled": True}
    created = "Đã có sẵn" if before["existing"] else "Đã tạo"
    return StepOutcome("done", f"{created} tài khoản {sam} và bật remote mailbox.", out, before_state=before)


# ------------------------------------------------------------------ m365_license
# StepDefinition is static: default entra_wait_hours (3) + mailbox_wait_hours (2); settings only tune messages.
M365_WAIT_SECONDS = 5 * 3600


def _skus(plan: dict[str, Any]) -> list[tuple[str, str]]:
    skus = list(plan.get("license_skus") or [])
    names = list(plan.get("license_names") or [])
    return [(sku, names[i] if i < len(names) and names[i] else sku) for i, sku in enumerate(skus)]


async def preview_m365_license(ctx: StepContext) -> list[str]:
    names = ", ".join(n for _, n in _skus(ctx.outputs.get("account_plan") or {})) or "không có"
    return [
        f"Gán license Microsoft 365 theo chức danh: {names} (chỉ license còn thiếu).",
        "Chờ mailbox Exchange Online sẵn sàng.",
    ]


async def m365_license(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("skipped", DISABLED)
    plan = ctx.outputs.get("account_plan") or {}
    wanted = _skus(plan)
    if not wanted:
        return StepOutcome("skipped", "Hàng ma trận không có license – bỏ qua.")
    eid = str(plan.get("employee_id") or "")
    if not EMPLOYEE_ID.match(eid):
        return StepOutcome("failed", BAD_ID)
    before: dict[str, Any] | None = None
    try:
        async with graph(ctx, Purpose.WRITE) as g:  # one poll = one client
            found = await g.find_user_by_employee_id(eid)
            if not found:
                if _waited(ctx) > s.entra_wait_hours * 3600:
                    return StepOutcome(
                        "failed",
                        f"Quá {s.entra_wait_hours} giờ user {plan.get('upn') or eid} chưa lên Entra ID.",
                    )
                return StepOutcome(
                    "waiting",
                    f"Chờ Entra Connect đồng bộ user {plan.get('upn') or eid} lên Entra ID …",
                    retry_in_seconds=300,
                )
            if len(found) > 1:
                return StepOutcome("failed", f"Mã NV {eid} có {len(found)} user trên Entra – xử lý thủ công.")
            user = found[0]
            uid = user["id"]
            if str(user.get("userPrincipalName") or "").lower() != str(plan.get("upn") or "").lower():
                return StepOutcome(
                    "failed",
                    f"User Entra có Mã NV {eid} mang UPN {user.get('userPrincipalName')}, "
                    f"khác UPN trong kế hoạch {plan.get('upn')} – kiểm tra thủ công.",
                )
            have = {str(a.get("skuId")).lower() for a in user.get("assignedLicenses") or []}
            missing = [(sku, name) for sku, name in wanted if sku.lower() not in have]
            tenant = {
                str(k["skuId"]).lower(): int(k["enabled"]) - int(k["consumed"])
                for k in (await g.subscribed_skus() if missing else [])
            }
            for sku, name in missing:
                if sku.lower() not in tenant:
                    return StepOutcome("failed", f"SKU {name} ({sku}) không có trong tenant Microsoft 365.")
            for sku, name in missing:
                if tenant[sku.lower()] <= 0:
                    return StepOutcome(
                        "waiting",
                        f"Hết license {name} – mua thêm hoặc giải phóng license.",
                        retry_in_seconds=1800,
                    )
            if missing:
                before = {
                    "assigned": [a.get("skuId") for a in user.get("assignedLicenses") or []],
                    "usage_location": user.get("usageLocation"),
                }
                if str(user.get("usageLocation") or "").upper() != s.usage_location.upper():
                    await g.set_usage_location(uid, s.usage_location.upper())
                await g.assign_licenses(uid, [sku for sku, _ in missing])
            user = await g.get_user(uid) or user
    except AppError:
        raise
    except Exception as exc:
        return StepOutcome("failed", f"Không gán được license: {_err(exc)}", before_state=before)
    if not any(
        p.get("service") == "exchange"
        and p.get("capabilityStatus") == "Enabled"
        and p.get("provisioningStatus") == "Success"
        for p in user.get("provisionedPlans") or []
    ):
        if _waited(ctx) > (s.entra_wait_hours + s.mailbox_wait_hours) * 3600:
            return StepOutcome(
                "failed",
                f"Quá {s.mailbox_wait_hours} giờ mailbox Exchange Online chưa sẵn sàng.",
                before_state=before,
            )
        return StepOutcome(
            "waiting", "Chờ mailbox Exchange Online …", before_state=before, retry_in_seconds=300
        )
    out = {
        "entra_id": uid,
        "upn": user.get("userPrincipalName"),
        "mail": user.get("mail") or user.get("userPrincipalName"),
        "assigned_skus": [sku for sku, _ in wanted],
    }
    return StepOutcome("done", f"Đã có license và mailbox {out['mail']}.", out, before_state=before)


# ------------------------------------------------------------------ it_handover
async def prepare_it_handover(ctx: StepContext) -> StepOutcome:
    software = list((ctx.outputs.get("account_plan") or {}).get("software") or [])
    return StepOutcome("waiting", f"Cần cài {len(software)} phần mềm.", {"software": software})


async def run_it_handover(ctx: StepContext) -> StepOutcome:
    """Never takes or stores a password: the temporary one is set on ADUC by the operator."""
    software = list((ctx.step.output or {}).get("software") or [])
    installed = {str(x) for x in ctx.input.get("installed") or []}
    skipped = {
        str(i.get("name")): str(i.get("note") or "").strip()
        for i in ctx.input.get("not_needed") or []
        if isinstance(i, dict)
    }
    bad = [n for n in skipped if n in software and not skipped[n]]
    if bad:
        raise AppError("Ghi chú lý do cho phần mềm không cần cài: " + ", ".join(bad), code="note_required")
    missing = [n for n in software if n not in installed and n not in skipped]
    if missing:
        raise AppError(
            "Chưa xác nhận cài hoặc đánh dấu không cần: " + ", ".join(missing), code="software_unconfirmed"
        )
    out = {
        "installed": [n for n in software if n in installed],
        "not_needed": [
            {"name": n, "note": skipped[n]} for n in software if n in skipped and n not in installed
        ],
    }
    return StepOutcome(
        "done", f"Đã bàn giao máy: {len(out['installed'])}/{len(software)} phần mềm đã cài.", out
    )


# ------------------------------------------------------------------ rtr_checklist
def _transfer_lines(o: dict[str, Any]) -> list[str]:
    plan, adt = o.get("transfer_plan") or {}, o.get("ad_transfer") or {}
    lic, bio, assets = (
        o.get("m365_relicense") or {},
        o.get("biostar_transfer") or {},
        o.get("transfer_assets") or {},
    )
    lines: list[str] = []
    if plan.get("target_ou"):
        lines.append(f"OU: {plan.get('ou') or '—'} → {plan['target_ou']}")
    if plan:
        n_add, n_rm = (
            adt.get("added", len(plan.get("add") or [])),
            adt.get("removed", len(plan.get("remove") or [])),
        )
        lines.append(f"Nhóm AD: thêm {n_add}, gỡ {n_rm}")
    cur, attrs = plan.get("current") or {}, plan.get("attributes") or {}
    for key, label in (("title", "Chức danh"), ("department", "Phòng ban")):
        if attrs.get(key):
            lines.append(f"{label}: {cur.get(key) or '—'} → {attrs[key]}")
    if attrs.get("manager") or attrs.get("manager_employee_id"):
        lines.append("Quản lý trực tiếp: đã cập nhật")
    if lic.get("added") or lic.get("removed"):
        lines.append(f"License: thêm {len(lic.get('added') or [])}, gỡ {len(lic.get('removed') or [])}")
    if bio.get("changed"):
        lines.append(
            f"BioStar: thêm {len(bio.get('added') or [])} nhóm quyền, gỡ {len(bio.get('removed') or [])}"
        )
    if assets.get("needs"):
        lines.append(
            "Tài sản cần bổ sung: " + ", ".join(f"{n['label']} x{n['quantity']}" for n in assets["needs"])
        )
    if assets.get("excess"):
        lines.append(
            "Tài sản vượt chuẩn: "
            + ", ".join(f"{a['name']} ({a.get('tag') or '—'})" for a in assets["excess"])
        )
    return lines


def build_rtr_summary(ctx: StepContext, kind: str) -> str:
    """Plain Vietnamese text HR copies into the RTR system (shared by onboard and offboard)."""
    emp, o = _emp(ctx), ctx.outputs
    plan, lic = o.get("account_plan") or {}, o.get("m365_license") or {}
    title = {"onboard": "Onboard", "offboard": "Offboard", "transfer": "Thuyên chuyển"}[kind]
    lines = [f"{title} – {emp.get('full_name') or ''} (Mã NV {emp.get('id') or ''})"]
    if kind == "transfer":
        return "\n".join([*lines, *_transfer_lines(o)])
    if kind == "offboard":
        acc = o.get("account_disable") or {}
        if acc:
            lines.append(f"Đã vô hiệu tài khoản: {acc.get('sam') or '—'}; UPN: {acc.get('upn') or '—'}")
        if o.get("ad_move_ban"):
            lines.append("Đã chuyển tài khoản vào Ban_Users")
        if (o.get("biostar_offboard") or {}).get("action"):
            lines.append(f"BioStar: {o['biostar_offboard']['action']}")
        if "return_assets" in o:
            n = len((o["return_assets"] or {}).get("returned_ids") or [])
            lines.append(f"Tài sản đã thu hồi về kho: {n}")
        if (o.get("return_doc") or {}).get("number"):
            lines.append(f"Biên bản thu hồi: {o['return_doc']['number']}")
        return "\n".join(lines)
    if plan.get("sam") or lic.get("upn"):
        lines.append(
            f"Tài khoản AD: {plan.get('sam') or '—'}; UPN: {lic.get('upn') or plan.get('upn') or '—'}"
        )
    if lic.get("mail"):
        lines.append(f"Email: {lic['mail']}")
    if plan.get("license_names"):
        lines.append("License: " + ", ".join(plan["license_names"]))
    if o.get("biostar_user"):
        lines.append(f"User BioStar: {emp.get('id')}")
    if (o.get("handover_doc") or {}).get("number"):
        lines.append(f"Biên bản bàn giao: {o['handover_doc']['number']}")
    assets = (o.get("assign_assets") or {}).get("assigned") or []
    if assets:
        lines.append("Tài sản: " + ", ".join(f"{a['name']} ({a.get('tag') or '—'})" for a in assets))
    return "\n".join(lines)


def _rtr_kind(ctx: StepContext) -> str:
    kind = ctx.params.get("kind")
    return kind if kind in ("offboard", "transfer") else "onboard"


async def prepare_rtr_checklist(ctx: StepContext) -> StepOutcome:
    return StepOutcome(
        "waiting", "Sao chép tóm tắt vào RTR.", {"summary": build_rtr_summary(ctx, _rtr_kind(ctx))}
    )


async def run_rtr_checklist(ctx: StepContext) -> StepOutcome:
    return StepOutcome("done", "Đã cập nhật RTR.", {"summary": build_rtr_summary(ctx, _rtr_kind(ctx))})


# ------------------------------------------------------------------ offboard
def _split_dn(dn: str) -> tuple[str, str]:
    """(leaf RDN, parent DN), splitting only on unescaped commas."""
    parts = re.split(r"(?<!\\),", dn, maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _guard(
    users: list[dict[str, Any]], ctx: StepContext, s: LifecycleSettings
) -> tuple[dict[str, Any] | None, str]:
    """The one AD account we may touch for this employee, or why we refuse."""
    emp = _emp(ctx)
    eid = str(emp.get("id") or "")
    if not EMPLOYEE_ID.match(eid):
        return None, BAD_ID
    if len(users) != 1:
        found = ", ".join(f"{u.get('sAMAccountName')} ({u['dn']})" for u in users) or "không có"
        return (
            None,
            f"Mã NV {eid} phải có đúng 1 tài khoản AD, hiện có {len(users)}: {found} – xử lý thủ công.",
        )
    u = users[0]
    if int(u.get("adminCount") or 0) == 1:
        return (
            None,
            f"Tài khoản {u.get('sAMAccountName')} là tài khoản đặc quyền (adminCount=1) – xử lý thủ công.",
        )
    if s.ad_base_ou and not inside(u["dn"], s.ad_base_ou):
        return None, f"Tài khoản {u['dn']} nằm ngoài OU gốc {s.ad_base_ou} – xử lý thủ công."
    if name_key(str(u.get("displayName") or "")) != name_key(str(emp.get("full_name") or "")):
        return None, (
            f"Tên trên AD “{u.get('displayName')}” không khớp họ tên trong request "
            f"“{emp.get('full_name')}” – kiểm tra Mã NV."
        )
    return u, ""


async def _entra(g: GraphClient, eid: str, upn: str | None) -> tuple[dict[str, Any] | None, str]:
    """The Entra user of this employee: by employeeId, else by the AD UPN; its UPN must equal the AD one."""
    found = await g.find_user_by_employee_id(eid)
    if len(found) > 1:
        return None, f"Mã NV {eid} có {len(found)} user trên Entra – xử lý thủ công."
    ent = found[0] if found else None
    if ent is None and upn and (by_upn := await g.find_user_by_upn(upn)):
        ent = by_upn
    if ent and upn and str(ent.get("userPrincipalName") or "").lower() != upn.lower():
        return (
            None,
            f"UPN trên Entra ({ent.get('userPrincipalName')}) khác UPN trên AD ({upn}) – xử lý thủ công.",
        )
    return ent, ""


NO_ENTRA = "Không thấy user trên Entra (theo Mã NV và UPN) – chưa kiểm tra / thu hồi phiên Entra."


async def account_disable(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("skipped", DISABLED)
    eid = str(_emp(ctx).get("id") or "")
    before: dict[str, Any] | None = None
    try:
        # AD first and independent of Graph: a Graph outage must never leave a leaver's account enabled.
        async with ad(ctx, Purpose.WRITE) as c:
            user, why = _guard(await c.find_users(eid) if EMPLOYEE_ID.match(eid) else [], ctx, s)
            if user is None:
                return StepOutcome("failed", why)
            dn, uac = user["dn"], int(user.get("userAccountControl") or 0)
            before = {
                "dn": dn,
                "ou": _split_dn(dn)[1],
                "uac": uac,
                "memberOf": list(user.get("memberOf") or []),
            }
            newly = not uac & 0x2
            if newly:
                await c.disable(dn)  # reads UAC again, flips only the disabled bit
    except AppError:
        raise
    except Exception as exc:
        return StepOutcome("failed", f"Không vô hiệu được tài khoản: {_err(exc)}", before_state=before)
    out = {"dn": dn, "sam": user.get("sAMAccountName"), "upn": user.get("userPrincipalName")}
    try:
        async with graph(ctx, Purpose.WRITE) as g:
            ent, why = await _entra(g, eid, out["upn"])
            if why:
                return StepOutcome("failed", f"AD đã vô hiệu. {why}", before_state=before)
            entra_id = ent["id"] if ent else None
            out |= {"entra_id": entra_id, "entra_checked": bool(entra_id)}
            if entra_id:
                before["entra_id"] = entra_id
                if newly:
                    with contextlib.suppress(
                        Exception
                    ):  # best effort: the final revoke below is the one that counts
                        await g.revoke_sessions(entra_id)
                cur = await g.get_user(entra_id)
                if cur is not None and cur.get("accountEnabled") is not False:
                    return StepOutcome(
                        "waiting",
                        "Chờ Entra Connect đồng bộ trạng thái vô hiệu lên Entra ID …",
                        before_state=before,
                        retry_in_seconds=300,
                    )
                try:
                    await g.revoke_sessions(entra_id)
                except ConnectorHttpError as exc:
                    if exc.status != 404:  # user already gone from Entra: nothing left to revoke
                        raise
    except Exception as exc:
        return StepOutcome(
            "waiting",
            f"AD đã vô hiệu, chưa thu hồi phiên Entra: {_err(exc)}",
            before_state=before,
            retry_in_seconds=300,
        )
    msg = (
        f"Đã vô hiệu tài khoản {out['sam']} trên AD và Entra."
        if out["entra_checked"]
        else (f"Đã vô hiệu tài khoản {out['sam']} trên AD. {NO_ENTRA}")
    )
    return StepOutcome("done", msg, out, before_state=before)


async def _preview_account(ctx: StepContext) -> list[str]:
    """What will really be touched: read the live AD account through the same guard the step uses."""
    s = await load_module_settings(ctx.session)
    eid = str(_emp(ctx).get("id") or "")
    try:
        async with ad(ctx, Purpose.READ) as c:
            u, why = _guard(await c.find_users(eid) if EMPLOYEE_ID.match(eid) else [], ctx, s)
    except Exception as exc:
        return [f"Không đọc được tài khoản AD của Mã NV {eid}: {_err(exc)}"]
    if u is None:
        return [f"Không thể thao tác: {why}"]
    state = "đã vô hiệu" if int(u.get("userAccountControl") or 0) & 0x2 else "đang hoạt động"
    return [
        f"Tài khoản: {u.get('sAMAccountName')} ({u.get('userPrincipalName')}) – {u.get('displayName')}, {state}.",
        f"OU hiện tại: {_split_dn(u['dn'])[1]}; thuộc {len(u.get('memberOf') or [])} nhóm.",
    ]


async def preview_account_disable(ctx: StepContext) -> list[str]:
    return [
        *await _preview_account(ctx),
        "Vô hiệu tài khoản AD, thu hồi phiên đăng nhập Entra, chờ Entra ID báo đã vô hiệu rồi thu hồi lần nữa.",
    ]


async def prepare_mailbox_handover(ctx: StepContext) -> StepOutcome:
    if not (ctx.params.get("options") or {}).get("mailbox_handover"):
        return StepOutcome("waiting", "Quản lý không yêu cầu chuyển mailbox – bấm Bỏ qua bước này.")
    return StepOutcome(
        "waiting",
        "Chuyển mailbox theo yêu cầu: on-prem `Set-RemoteMailbox <user> -Type Shared`, "
        "hoặc Exchange Online `Set-Mailbox <user> -Type Shared` / thiết lập forward.",
    )


async def run_mailbox_handover(ctx: StepContext) -> StepOutcome:
    # The engine cannot finish a manual step in prepare, so a step nobody asked for just completes here.
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("done", DISABLED)
    if not (ctx.params.get("options") or {}).get("mailbox_handover"):
        return StepOutcome("done", "Không yêu cầu chuyển mailbox.")
    return StepOutcome("done", "Đã chuyển mailbox.")


RELEASE_KEY = "lifecycle.mailbox_release"
BAN_WARNING = "Chuyển vào Ban_Users: Entra sẽ xoá user, mailbox và license (khôi phục được trong 30 ngày)"


async def schedule_release(ctx: StepContext, s: LifecycleSettings, dn: str, upn: str | None) -> date:
    """Create (once per request) the scheduled job that moves the account to Ban_Users when retention ends."""
    when = today_vn() + timedelta(days=s.mailbox_retention_days)
    key = f"lifecycle:mailbox_release:{ctx.params.get('request_id') or ctx.job.id}"
    keep = ("kind", "request_id", "request_display_id", "employee")
    job, new = await engine.create_job(
        ctx.session,
        ctx.actor,
        RELEASE_KEY,
        params={**{k: ctx.params.get(k) for k in keep}, "microsoft": True, "dn": dn, "upn": upn},
        subject_key=ctx.job.subject_key,
        subject_label=ctx.job.subject_label,
        external_ref=ctx.job.external_ref,
        idempotency_key=key,
    )
    if new:
        await ctx.session.flush()
        await engine.start_job(ctx.session, ctx.actor, job, when)
        if ctx.job.assignee_name:
            await engine.set_assignee(
                ctx.session,
                job,
                user_id=ctx.job.assignee_user_id,
                name=ctx.job.assignee_name,
                actor="system",
                source="system",
            )
    return job.scheduled_for or when


def _keeps_mailbox(ctx: StepContext) -> bool:
    return bool((ctx.params.get("options") or {}).get("mailbox_handover"))


async def _move_ban(ctx: StepContext, *, final: bool) -> StepOutcome:
    """Move the disabled account out of the live OU. With a kept mailbox (and not `final`) it goes to the
    retained OU (still synced, mailbox survives) and a scheduled job finishes the move to Ban_Users."""
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("skipped", DISABLED)
    if not s.ad_ban_ou:
        return StepOutcome("failed", "Chưa cấu hình OU Ban_Users (ad_ban_ou) trong Cấu hình module.")
    keep = _keeps_mailbox(ctx) and not final
    if keep and not s.ad_retained_ou:
        return StepOutcome(
            "failed",
            "Quản lý yêu cầu giữ mailbox nhưng chưa cấu hình OU giữ mailbox (ad_retained_ou) trong Cấu hình module.",
        )
    eid = str(_emp(ctx).get("id") or "")
    before: dict[str, Any] | None = None
    try:
        async with ad(ctx, Purpose.WRITE) as c, graph(ctx, Purpose.WRITE) as g:
            user, why = _guard(await c.find_users(eid) if EMPLOYEE_ID.match(eid) else [], ctx, s)
            if user is None:
                return StepOutcome("failed", why)
            upn = user.get("userPrincipalName")
            ent, why = await _entra(g, eid, upn)
            if why:
                return StepOutcome("failed", why)
            if not int(user.get("userAccountControl") or 0) & 0x2:
                return StepOutcome("failed", "Tài khoản chưa bị vô hiệu – chạy bước vô hiệu tài khoản trước.")
            dn = user["dn"]
            keep = keep and not inside(dn, s.ad_ban_ou)  # already in Ban_Users: nothing to keep
            target = s.ad_retained_ou if keep else s.ad_ban_ou
            before = {
                "dn": dn,
                "ou": _split_dn(dn)[1],
                "licenses": [a.get("skuId") for a in (ent or {}).get("assignedLicenses") or []],
            }
            if ent:
                before["entra_id"] = ent["id"]
            if not inside(dn, target):
                rdn = _split_dn(dn)[0]
                new_rdn = None
                if await c.get(f"{rdn},{target}") is not None:  # same CN already there: add the employee id
                    new_rdn = f"{rdn} ({eid})"
                    before["rdn"] = rdn
                    if await c.get(f"{new_rdn},{target}") is not None:
                        return StepOutcome(
                            "failed", f"Đã có tài khoản {new_rdn} trong {target} – xử lý thủ công."
                        )
                await c.move(dn, target, new_rdn)
                dn = f"{new_rdn or rdn},{target}"
            if not keep and ent and not await g.deleted_user(ent["id"]):
                return StepOutcome(
                    "waiting",
                    "Chờ Entra Connect xoá user khỏi Entra ID (OU Ban_Users ngoài phạm vi đồng bộ) …",
                    before_state=before,
                    retry_in_seconds=600,
                )
            release = await schedule_release(ctx, s, dn, upn) if keep else None
    except AppError:
        raise
    except Exception as exc:
        return StepOutcome("failed", f"Không chuyển được tài khoản: {_err(exc)}", before_state=before)
    out: dict[str, Any] = {"dn": dn, "ou": target, **{k: before.get(k) for k in ("entra_id", "licenses")}}
    out["entra_checked"] = bool(ent)
    if release:
        out["release_date"] = release.isoformat()
        msg = (
            f"Đã chuyển tài khoản vào OU giữ mailbox {target}. "
            f"Job “Hết hạn giữ mailbox” sẽ chuyển vào Ban_Users ngày {release:%d/%m/%Y}."
        )
    else:
        msg = f"Đã chuyển tài khoản vào {target}." + ("" if ent else f" {NO_ENTRA}")
    return StepOutcome("done", msg, out, before_state=before)


async def ad_move_ban(ctx: StepContext) -> StepOutcome:
    return await _move_ban(ctx, final=False)


async def ad_move_ban_final(ctx: StepContext) -> StepOutcome:
    return await _move_ban(ctx, final=True)


async def _preview_move(ctx: StepContext, *, final: bool) -> list[str]:
    s = await load_module_settings(ctx.session)
    if _keeps_mailbox(ctx) and not final:
        dest = [
            f"Chuyển vào OU giữ mailbox {s.ad_retained_ou or '(chưa cấu hình)'} (user và mailbox được giữ).",
            f"Sau {s.mailbox_retention_days} ngày Job “Hết hạn giữ mailbox” sẽ chuyển vào Ban_Users.",
        ]
    else:
        dest = [
            f"{BAN_WARNING}. Đích: {s.ad_ban_ou or '(chưa cấu hình)'}.",
            "Chờ Entra ID xoá user (license được thu hồi theo).",
        ]
    return [*await _preview_account(ctx), *dest]


async def preview_ad_move_ban(ctx: StepContext) -> list[str]:
    return await _preview_move(ctx, final=False)


async def preview_ad_move_ban_final(ctx: StepContext) -> list[str]:
    return await _preview_move(ctx, final=True)


# ------------------------------------------------------------------ transfer (thuyên chuyển)
TRANSFER_WAIT = 3600  # seconds between checks while waiting for the effective date


def not_yet_effective(iso: str | None) -> StepOutcome | None:
    """Transfer steps change access exactly on the effective date: waiting until then (None = go ahead)."""
    eff = date.fromisoformat(iso) if iso else None
    if eff and today_vn() < eff:
        return StepOutcome("waiting", f"Chờ ngày hiệu lực {eff:%d/%m/%Y}", retry_in_seconds=TRANSFER_WAIT)
    return None


def _ou_options(s: LifecycleSettings) -> list[str]:
    """OUs a transfer may target: the configured ones, never Ban_Users or the retained-mailbox OU."""
    return [
        o
        for o in dict.fromkeys([s.ad_default_ou, *(r.ou for r in s.ad_ou_map)])
        if o
        and not (s.ad_ban_ou and inside(o, s.ad_ban_ou))
        and not (s.ad_retained_ou and inside(o, s.ad_retained_ou))
        and (not s.ad_base_ou or inside(o, s.ad_base_ou))
    ]


async def _group_problem(c: AdClient, dn: str, s: LifecycleSettings) -> str:
    """Why we must not touch this group ('' = fine): it must be a real, non-privileged group in the group base."""
    g = await c.get_group(dn)
    if g is None:
        return "không tồn tại trên AD"
    oc = g.get("objectClass")
    classes = {str(x).lower() for x in ([oc] if isinstance(oc, str) else oc or [])}
    if "group" not in classes:
        return "không phải nhóm AD"
    if int(g.get("adminCount") or 0) == 1:
        return "là nhóm đặc quyền (adminCount=1)"
    if s.ad_group_base and not inside(dn, s.ad_group_base):
        return f"nằm ngoài OU nhóm {s.ad_group_base}"
    return ""


async def biostar_client(ctx: StepContext, purpose: Purpose) -> BioStarClient:
    s = await load_module_settings(ctx.session)
    _, conn = await open_connection(ctx.session, "biostar", purpose, **main_biostar(s))
    return BioStarClient(conn)


def _attr_same(key: str, want: str, have: Any) -> bool:
    return tr.same_dn(want, str(have or "")) if key == "manager" else want == str(have or "")


def _has(dn: str, dns: list[str]) -> bool:
    return any(tr.same_dn(dn, d) for d in dns)


async def _plan_licenses(ctx: StepContext, s: LifecycleSettings, eid: str, upn: str | None, org: dict | None, ad_dept: str, warnings: list[str]) -> dict[str, Any]:  # fmt: skip
    """Read-only license diff: add the new level's SKUs, remove only matrix SKUs assigned directly (not by a group)."""
    emp, matrix = _emp(ctx), s.allocation_matrix
    out: dict[str, Any] = {"add": [], "remove": [], "level": "", "names": {}}
    if matrix is None or not emp.get("job_title"):
        return out
    dept = (org or {}).get("department") or ad_dept.split(" – ")[0]
    level, level_why = matrix.level_for(emp["job_title"], None, dept)
    if not tr.license_changes([], level, matrix)["add"]:
        return out
    fallback = (
        level_why == "hàng mặc định"
    )  # the title matched no level: never remove a license on a guess (C-21)
    if fallback:
        warnings.append("Chức danh không khớp cấp bậc nào – kiểm tra tay license (không gỡ license tự động).")
    try:
        async with graph(ctx, Purpose.READ) as g:
            ent, why = await _entra(g, eid, upn)
    except Exception as exc:
        warnings.append(f"Không đọc được license trên Entra: {_err(exc)}")
        return out
    if ent is None:
        warnings.append(why or f"Không thấy user Entra của Mã NV {eid} – chưa lập dòng license.")
        return out
    current = [str(a.get("skuId")) for a in ent.get("assignedLicenses") or []]
    direct = [
        str(x.get("skuId")) for x in ent.get("licenseAssignmentStates") or [] if not x.get("assignedByGroup")
    ]
    diff = tr.license_changes(current, level, matrix, direct)
    if fallback:
        diff["remove"] = []
    names = {
        k.lower(): n for lv in matrix.levels for k, n in zip(lv.license_skus, lv.license_names, strict=False)
    }
    return {
        **diff,
        "level": level.name,
        "level_why": level_why,
        "names": {k: names.get(k, k) for k in [*diff["add"], *diff["remove"]]},
    }


async def _plan_biostar(ctx: StepContext, s: LifecycleSettings, eid: str, emp_org: dict, known: frozenset[str], warnings: list[str]) -> dict[str, list[str]]:  # fmt: skip
    """Read-only BioStar diff of access groups (the run replays only this, intersected with the current state)."""
    out: dict[str, list[str]] = {"add": [], "remove": []}
    if not tr.managed_biostar(s):
        return out
    try:
        async with await biostar_client(ctx, Purpose.READ) as c:
            user = await c.get_user(eid)
    except Exception as exc:
        warnings.append(f"Không đọc được BioStar: {_err(exc)}")
        return out
    if user is None or str(user.get("disabled")).lower() == "true":
        return out
    if not isinstance(user.get("access_groups"), list):
        warnings.append("BioStar không trả danh sách nhóm quyền – không lập dòng BioStar.")
        return out
    current = [str(g["id"]) for g in user["access_groups"] if isinstance(g, dict) and "id" in g]
    new, _ = tr.biostar_groups(current, emp_org, s, known)
    return {"add": [i for i in new if i not in current], "remove": [i for i in current if i not in new]}


async def prepare_transfer_plan(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("skipped", DISABLED)
    emp, eid = _emp(ctx), str(_emp(ctx).get("id") or "")
    warnings: list[str] = []
    raw_eff, eff = ctx.params.get("effective_date"), None
    try:
        eff = date.fromisoformat(str(raw_eff)) if raw_eff else None
    except ValueError:
        warnings.append(f"Ngày hiệu lực “{raw_eff}” không hợp lệ.")
    if eff is None:
        warnings.append("Chưa có ngày hiệu lực – nhập ngày hiệu lực trước khi chốt kế hoạch.")
    try:
        async with ad(ctx, Purpose.READ) as c:
            u, why = _guard(await c.find_users(eid) if EMPLOYEE_ID.match(eid) else [], ctx, s)
            if u is None:
                return StepOutcome(
                    "waiting", f"Không lập được kế hoạch: {why}", {"blocked": True, "warnings": [why]}
                )
            cur_ou = _split_dn(u["dn"])[1]
            built = tr.full_org(emp, ctx.params.get("employee_old") or {}, str(u.get("department") or ""), s)
            org, known, org_src = built if built else (None, frozenset[str](), "request")
            emp_org = tr.org_employee(org) if org else {}
            ou, groups = None, {"add": [], "remove": []}
            sources = {**(ctx.params.get("sources") or {}), "org": org_src, "current": "ad"}
            if org:
                ou = tr.target_ou(emp_org, s, known)
                if tr.incomplete(emp_org, s, known):
                    warnings.append(
                        "Chưa đủ thông tin tổ chức (thiếu: "
                        + ", ".join(k for k in tr.FIELDS if k not in known)
                        + ") – không gỡ nhóm / không đề xuất OU thuộc quy tắc phụ thuộc phần thiếu."
                    )
                if ou and (tr.same_dn(ou, cur_ou) or not any(tr.same_dn(ou, o) for o in _ou_options(s))):
                    if not tr.same_dn(ou, cur_ou):
                        warnings.append(
                            f"OU đích {ou} không nằm trong danh sách OU đã cấu hình – giữ nguyên OU."
                        )
                    ou = None
                changes = tr.group_changes(list(u.get("memberOf") or []), emp_org, s, known)
                for kind in ("add", "remove"):
                    for g in changes[kind]:
                        if bad := await _group_problem(c, g, s):
                            warnings.append(f"Bỏ qua nhóm {g}: {bad}.")
                        else:
                            groups[kind].append(g)
            attrs = tr.attribute_changes(u, {**emp, "department": tr.department_text(org or {})})
            if mid := attrs.pop("manager_employee_id", None):
                found = await c.find_users(mid) if EMPLOYEE_ID.match(mid) else []
                if len(found) != 1:
                    warnings.append(
                        f"Không xác định được quản lý mới (Mã NV {mid}) trên AD – giữ nguyên manager."
                    )
                elif not tr.same_dn(found[0]["dn"], str(u.get("manager") or "")):
                    attrs["manager_employee_id"] = mid  # pinned by employee id: the DN is resolved at run
    except AppError:
        raise
    except Exception as exc:
        return StepOutcome(
            "waiting", f"Không đọc được AD: {_err(exc)}", {"blocked": True, "warnings": [_err(exc)]}
        )
    licenses = await _plan_licenses(ctx, s, eid, u.get("userPrincipalName"), org, str(u.get("department") or ""), warnings)  # fmt: skip
    biostar = await _plan_biostar(ctx, s, eid, emp_org, known, warnings) if org else {"add": [], "remove": []}
    out = {
        "blocked": False,
        "pinned": False,  # step output is merged: a stale pin (after a rejected verify) must not survive (C-22)
        "employee_id": eid,
        "dn": u["dn"],
        "upn": u.get("userPrincipalName"),
        "ou": cur_ou,
        "target_ou": ou,
        "ou_options": _ou_options(s),
        "org": org,
        "add": groups["add"],
        "remove": groups["remove"],
        "attributes": attrs,
        "current": {k: u.get(k) or "" for k in ("title", "department", "manager")},
        "licenses": licenses,
        "biostar": biostar,
        "effective_date": eff.isoformat() if eff else None,
        "sources": sources,
        "warnings": warnings,
    }
    return StepOutcome(
        "waiting",
        f"Thêm {len(out['add'])} nhóm, gỡ {len(out['remove'])} nhóm, "
        f"{'chuyển OU' if ou else 'giữ OU'}, đổi {len(attrs)} thuộc tính, "
        f"license +{len(licenses['add'])}/-{len(licenses['remove'])}, "
        f"BioStar +{len(biostar['add'])}/-{len(biostar['remove'])}.",
        out,
    )


def _pick(plan_items: list[str], given: Any, code: str, what: str) -> list[str]:
    """The lines the person kept: a deduplicated subset of the plan's own lines (None = all)."""
    base = list(plan_items)
    if given is None:
        return base
    kept = list(dict.fromkeys(str(g) for g in given))
    if stray := [g for g in kept if g not in base]:
        raise AppError(f"{what} không nằm trong kế hoạch: " + ", ".join(stray), code=code)
    return kept


async def run_transfer_plan(ctx: StepContext) -> StepOutcome:
    """Manual completion (API request, no external calls): pin the removals, OU and effective date confirmed."""
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("done", DISABLED)
    plan = dict(ctx.step.output or {})
    if "dn" not in plan or plan.get("blocked"):
        raise AppError(
            "Chưa có kế hoạch thuyên chuyển hợp lệ (bước chuẩn bị chưa xong).", code="plan_not_ready"
        )
    eff = plan.get("effective_date") or ctx.input.get("effective_date")
    try:
        eff = date.fromisoformat(str(eff)).isoformat()
    except ValueError:
        raise AppError("Cần nhập ngày hiệu lực (yyyy-mm-dd).", code="effective_date_required") from None
    remove = list(plan.get("remove") or [])
    kept = _pick(remove, ctx.input.get("remove"), "group_not_in_plan", "Nhóm")
    lic, bio = dict(plan.get("licenses") or {}), dict(plan.get("biostar") or {})
    lic_kept = _pick(
        lic.get("remove") or [], ctx.input.get("remove_licenses"), "license_not_in_plan", "License"
    )
    bio_kept = _pick(bio.get("remove") or [], ctx.input.get("remove_biostar"), "access_group_not_in_plan", "Nhóm BioStar")  # fmt: skip
    ou = ctx.input.get("ou") or plan.get("target_ou")
    changed_ou = (ou or None) != (plan.get("target_ou") or None)
    if changed_ou and ou and not any(tr.same_dn(ou, o) for o in plan.get("ou_options") or []):
        raise AppError("OU không nằm trong danh sách OU đã cấu hình.", code="ou_not_configured")
    reduced = (
        set(kept) != set(remove)
        or set(lic_kept) != set(lic.get("remove") or [])
        or set(bio_kept) != set(bio.get("remove") or [])
    )
    if (changed_ou or reduced) and not await _can_assign(ctx):
        raise AppError(
            "Chỉ người có quyền phân công mới được đổi OU hoặc bỏ chọn dòng cần gỡ.", code="forbidden"
        )
    pinned = {
        **plan,
        "remove": kept,
        "target_ou": ou or None,
        "licenses": {**lic, "remove": lic_kept},
        "biostar": {**bio, "remove": bio_kept},
        "effective_date": eff,
        "pinned": True,
    }
    n = len(kept) + len(lic_kept) + len(bio_kept)
    return StepOutcome("done", f"Đã chốt kế hoạch (hiệu lực {date.fromisoformat(eff):%d/%m/%Y}): gỡ {n} mục.", pinned)  # fmt: skip


def pinned_transfer(ctx: StepContext) -> dict[str, Any]:
    plan = ctx.outputs.get("transfer_plan") or {}
    if not plan.get("pinned"):
        raise AppError("Chưa có kế hoạch thuyên chuyển đã chốt.", code="plan_missing")
    return plan


def effective_wait(plan: dict[str, Any]) -> StepOutcome | None:
    """Waiting outcome until the pinned effective date; a pinned plan without one never runs."""
    if not plan.get("effective_date"):
        return StepOutcome("failed", "Kế hoạch chưa có ngày hiệu lực – huỷ Job và tạo lại từ request.")
    return not_yet_effective(plan["effective_date"])


async def preview_ad_transfer(ctx: StepContext) -> list[str]:
    p = ctx.outputs.get("transfer_plan") or {}
    if not p.get("pinned"):
        return ["Chưa có kế hoạch thuyên chuyển đã chốt."]
    lines = await _preview_account(ctx)
    move = f"chuyển OU {p['ou']} → {p['target_ou']}" if p.get("target_ou") else "giữ nguyên OU"
    lines.append(f"Thêm {len(p['add'])} nhóm, gỡ {len(p['remove'])} nhóm, {move}.")
    if p.get("attributes"):
        lines.append("Cập nhật thuộc tính: " + ", ".join(p["attributes"]) + ".")
    if p.get("effective_date"):
        lines.append(f"Chỉ chạy từ ngày hiệu lực {date.fromisoformat(p['effective_date']):%d/%m/%Y}.")
    return lines


async def ad_transfer(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("skipped", DISABLED)
    p = pinned_transfer(ctx)
    if wait := effective_wait(p):
        return wait
    eid = str(p.get("employee_id") or "")
    before: dict[str, Any] | None = None
    warnings: list[str] = []
    try:
        async with ad(ctx, Purpose.WRITE) as c:
            # Re-read by employee id: the DN may have changed since the plan was made.
            user, why = _guard(await c.find_users(eid) if EMPLOYEE_ID.match(eid) else [], ctx, s)
            if user is None:
                return StepOutcome("failed", why)
            dn, member_of = user["dn"], list(user.get("memberOf") or [])
            before = {
                "dn": dn,
                "ou": _split_dn(dn)[1],
                "title": user.get("title"),
                "department": user.get("department"),
                "manager": user.get("manager"),
                "memberOf": member_of,
            }
            # Only groups the settings manage right now (C-18).
            remove = [g for g in p["remove"] if _has(g, tr.managed_groups(s))]
            for g in [*p["add"], *remove]:
                if bad := await _group_problem(c, g, s):
                    return StepOutcome(
                        "failed",
                        f"Nhóm {g} {bad} – kế hoạch đã chốt không còn đúng: huỷ Job và tạo lại từ request.",
                        before_state=before,
                    )
            add = [g for g in p["add"] if not _has(g, member_of)]
            for g in add:
                await c.add_member(g, dn)
            wanted = {
                k: v for k, v in (p.get("attributes") or {}).items() if v and k != "manager_employee_id"
            }
            if mid := (p.get("attributes") or {}).get("manager_employee_id"):
                found = await c.find_users(mid) if EMPLOYEE_ID.match(mid) else []
                if len(found) != 1:
                    warnings.append(f"Không xác định được quản lý (Mã NV {mid}) – giữ nguyên manager.")
                else:
                    wanted["manager"] = found[0]["dn"]
            changes = {k: v for k, v in wanted.items() if not _attr_same(k, v, user.get(k))}
            if changes:
                await c.modify(dn, changes)
            target = p.get("target_ou")
            if target and not _same_dn(_split_dn(dn)[1], target):
                if not any(_same_dn(target, o) for o in _ou_options(s)):
                    return StepOutcome("failed", f"OU {target} không hợp lệ.", before_state=before)
                rdn = _split_dn(dn)[0]
                new_rdn = None
                if await c.get(f"{rdn},{target}") is not None:  # same CN already there: add the employee id
                    new_rdn = f"{rdn} ({eid})"
                    before["rdn"] = rdn
                    if await c.get(f"{new_rdn},{target}") is not None:
                        return StepOutcome(
                            "failed",
                            f"Đã có tài khoản {new_rdn} trong {target} – xử lý thủ công.",
                            before_state=before,
                        )
                await c.move(dn, target, new_rdn)
                fresh = await c.find_users(eid)
                dn = fresh[0]["dn"] if len(fresh) == 1 else f"{new_rdn or rdn},{target}"
            # Only groups the settings manage right now, and only those the person still has.
            drop = [g for g in remove if _has(g, member_of)]
            for g in drop:
                await c.remove_member(g, dn)
            final_dn, final_groups = dn, [*member_of, *add]
            if drop:  # read back: remove_member tolerates "not a member", so check by employee id
                fresh = await c.find_users(eid)
                if len(fresh) != 1:
                    return StepOutcome(
                        "failed",
                        f"Không đọc lại được tài khoản Mã NV {eid} sau khi gỡ nhóm.",
                        before_state=before,
                    )
                final_dn, final_groups = fresh[0]["dn"], list(fresh[0].get("memberOf") or [])
                if left := [g for g in drop if _has(g, final_groups)]:
                    return StepOutcome(
                        "failed", "Nhóm vẫn còn sau khi gỡ: " + ", ".join(left), before_state=before
                    )
    except AppError:
        raise
    except Exception as exc:
        return StepOutcome(
            "failed", f"Không hoàn tất thuyên chuyển trên AD: {_err(exc)}", before_state=before
        )
    # Outputs compare the first-seen state (kept across retries) with the final one, not this run's intent.
    first = {**before, **(getattr(ctx.step, "before_state", None) or {})}
    first_groups = list(first.get("memberOf") or [])
    added = [g for g in p["add"] if not _has(g, first_groups) and _has(g, final_groups)]
    removed = [g for g in remove if _has(g, first_groups) and not _has(g, final_groups)]
    moved = not _same_dn(_split_dn(str(first["dn"]))[1], _split_dn(final_dn)[1])
    attrs = sorted(k for k, v in wanted.items() if not _attr_same(k, v, first.get(k)))
    out = {
        "dn": final_dn,
        "upn": user.get("userPrincipalName"),
        "added": len(added),
        "removed": len(removed),
        "attributes": attrs,
        "moved": moved,
        "warnings": warnings,
    }
    msg = f"Đã thêm {len(added)} nhóm, gỡ {len(removed)} nhóm, {'chuyển OU' if moved else 'giữ OU'}, đổi {len(attrs)} thuộc tính."
    return StepOutcome("done", msg, out, before_state=before)


# ------------------------------------------------------------------ m365_relicense (transfer)
async def preview_m365_relicense(ctx: StepContext) -> list[str]:
    lic = (ctx.outputs.get("transfer_plan") or {}).get("licenses") or {}
    names = lic.get("names") or {}
    show = lambda ks: ", ".join(names.get(k, k) for k in ks) or "—"  # noqa: E731
    return [
        f"Thêm license: {show(lic.get('add') or [])}; gỡ license: {show(lic.get('remove') or [])} "
        "(chỉ SKU trong ma trận được gán trực tiếp) trong một lần gọi.",
        "Chỉ chạy từ ngày hiệu lực; kiểm tra còn license trống trước khi thêm.",
    ]


async def m365_relicense(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    if not ms_enabled(s):
        return StepOutcome("skipped", DISABLED)
    plan = pinned_transfer(ctx)
    lic = plan.get("licenses") or {}
    if not (lic.get("add") or lic.get("remove")):
        return StepOutcome("skipped", "Kế hoạch không có thay đổi license – giữ nguyên.")
    if wait := effective_wait(plan):
        return wait
    names = lic.get("names") or {}
    eid, upn = str(plan.get("employee_id") or ""), (ctx.outputs.get("ad_transfer") or {}).get("upn") or plan.get("upn")  # fmt: skip
    if not EMPLOYEE_ID.match(eid) or not upn:
        return StepOutcome("failed", "Chưa có Mã NV hoặc UPN trên AD để tìm user Entra – xử lý thủ công.")
    before: dict[str, Any] | None = None
    try:
        async with graph(ctx, Purpose.WRITE) as g:
            ent, why = await _entra(g, eid, upn)
            if ent is None:
                return StepOutcome("failed", why or f"Không thấy user Entra của Mã NV {eid} (UPN {upn}).")
            current = [str(a.get("skuId")) for a in ent.get("assignedLicenses") or []]
            before = {"assigned": current}
            have = {x.lower() for x in current}
            direct = {
                str(x.get("skuId")).lower()
                for x in ent.get("licenseAssignmentStates") or []
                if not x.get("assignedByGroup")
            }
            add = [x for x in lic.get("add") or [] if x.lower() not in have]
            # Only pinned SKUs that are still direct and still managed by the matrix now.
            remove = [
                x
                for x in lic.get("remove") or []
                if x.lower() in direct and x.lower() in tr.managed_skus(s.allocation_matrix)
            ]
            if add:
                tenant = {
                    str(k["skuId"]).lower(): int(k["enabled"]) - int(k["consumed"])
                    for k in await g.subscribed_skus()
                }
                for sku in add:
                    if sku.lower() not in tenant:
                        return StepOutcome(
                            "failed",
                            f"SKU {names.get(sku, sku)} ({sku}) không có trong tenant Microsoft 365.",
                            before_state=before,
                        )
                for sku in add:
                    if tenant[sku.lower()] <= 0:
                        return StepOutcome(
                            "waiting",
                            f"Hết license {names.get(sku, sku)} – mua thêm hoặc giải phóng license.",
                            before_state=before,
                            retry_in_seconds=1800,
                        )
            if add or remove:
                await g.swap_licenses(ent["id"], add, remove)
    except AppError:
        raise
    except Exception as exc:
        return StepOutcome("failed", f"Không đổi được license: {_err(exc)}", before_state=before)
    out = {"entra_id": ent["id"], "upn": upn, "added": add, "removed": remove}
    msg = f"Đã thêm {len(add)} license, gỡ {len(remove)} license (cấp {lic.get('level') or '—'})."
    return StepOutcome("done", msg, out, before_state=before)
