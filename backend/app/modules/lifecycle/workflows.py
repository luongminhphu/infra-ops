"""Onboard / Offboard workflows (Microsoft steps run only when `microsoft_enabled` is on).

Every write step reads the current state first (idempotent on retry), keeps the previous state in
``before_state`` and never rolls back automatically.
"""

from __future__ import annotations

import html
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.connections.service import open_connection
from app.core.connectors.base import Purpose
from app.core.connectors.http import ConnectorHttpError
from app.core.connectors.sdp import SdpClient
from app.core.documents import service as documents
from app.core.errors import AppError, PersonalCredentialMissing
from app.core.storage.service import read_file
from app.core.timeutil import today_vn
from app.core.workflow.definitions import StepContext, StepDefinition, StepOutcome, WorkflowDefinition
from app.modules.lifecycle import normalize
from app.modules.lifecycle import transfer as tr
from app.modules.lifecycle.allocation import matches
from app.modules.lifecycle.documents import HANDOVER, RETURN
from app.modules.lifecycle.microsoft import (
    M365_WAIT_SECONDS,
    account_disable,
    ad_account,
    ad_move_ban,
    ad_move_ban_final,
    ad_transfer,
    effective_wait,
    m365_license,
    m365_relicense,
    pinned_transfer,
    prepare_account_plan,
    prepare_it_handover,
    prepare_mailbox_handover,
    prepare_rtr_checklist,
    prepare_transfer_plan,
    preview_account_disable,
    preview_ad_account,
    preview_ad_move_ban,
    preview_ad_move_ban_final,
    preview_ad_transfer,
    preview_m365_license,
    preview_m365_relicense,
    run_account_plan,
    run_it_handover,
    run_mailbox_handover,
    run_rtr_checklist,
    run_transfer_plan,
)
from app.modules.lifecycle.microsoft import (
    biostar_client as _biostar,
)
from app.modules.lifecycle.models import ReconRow, Snapshot
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import MODULE_ID
from app.modules.lifecycle.settings import load as load_module_settings

VIEW, EXECUTE, OVERRIDE = "lifecycle.job.view", "lifecycle.job.execute", "lifecycle.job.override_close"
# Without it an Operator sees only the jobs assigned to them and the unassigned ones they may take.
VIEW_ALL = "lifecycle.job.view_all"


def _fmt(iso: str | None) -> str:
    if not iso:
        return "?"
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}"


async def _sdp(ctx: StepContext, purpose: Purpose) -> SdpClient:
    """SDP as the person responsible for the Job (its assignee, else whoever started it, else whoever created
    it): their own API key reads and writes, so SDP records who did the work. A job the sync drafted has no
    human creator, so the person who pressed "Bắt đầu" is the fallback."""
    who = ctx.job.assignee_user_id or ctx.job.started_by or ctx.job.created_by
    s = await load_module_settings(ctx.session)
    _, conn = await open_connection(ctx.session, "sdp", purpose, profile_id=s.sdp_profile_id, as_user=who)
    return SdpClient(conn)


def _err(exc: Exception) -> str:
    if isinstance(exc, ConnectorHttpError):
        return f"{exc.message} {exc.hint}".strip()
    return getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"


def _emp(ctx: StepContext) -> dict[str, Any]:
    return ctx.params.get("employee") or {}


def _sdp_user_id(ctx: StepContext) -> str | None:
    return (ctx.outputs.get("find_sdp_user") or {}).get("sdp_user_id") or ctx.params.get("sdp_user_id")


# ------------------------------------------------------------------ common steps
async def check_request(ctx: StepContext) -> StepOutcome:
    rid = ctx.params["request_id"]
    s = await load_module_settings(ctx.session)
    try:
        async with await _sdp(ctx, Purpose.READ) as c:
            req = await c.get_request(rid)
    except Exception as exc:
        if isinstance(exc, PersonalCredentialMissing):
            raise  # the step waits for the person's own key (engine), it does not fail
        return StepOutcome("failed", f"Không đọc được request #{rid} trên SDP: {_err(exc)}")
    status = ((req.get("status") or {}).get("name")) or ""
    out = {"status": status, "subject": req.get("subject")}
    if s.is_closed(status):
        return StepOutcome(
            "failed",
            f"Request #{ctx.params.get('request_display_id') or rid} đang ở trạng thái “{status}” trên SDP. "
            "Mở lại request (Job tự huỷ ở lần đồng bộ tới và tự mở lại khi request mở lại) hoặc huỷ Job.",
            out,
        )
    return StepOutcome("done", f"Request còn mở (trạng thái “{status or 'không rõ'}”).", out)


async def preview_check_request(ctx: StepContext) -> list[str]:
    return [
        f"Đọc request #{ctx.params.get('request_display_id') or ctx.params['request_id']} trên SDP và kiểm tra request còn mở."
    ]


async def find_sdp_user(ctx: StepContext) -> StepOutcome:
    """Find the SDP user (imported from AD via LDAP) carrying the employee ID. Waits while it does not exist yet."""
    if uid := ctx.params.get("sdp_user_id"):
        # Chosen in the job: job title / site for the record come from the latest synced SDP user.
        run = await latest_run(ctx.session)
        snap = (
            await ctx.session.scalar(
                select(Snapshot).where(
                    Snapshot.run_id == run.id, Snapshot.system == "sdp_user", Snapshot.external_id == uid
                )
            )
            if run
            else None
        )
        known = snap.data if snap else {}
        return StepOutcome(
            "done",
            f"Dùng user SDP đã chọn: {ctx.params.get('sdp_user_name') or uid}.",
            {
                "sdp_user_id": uid,
                "sdp_user_name": ctx.params.get("sdp_user_name"),
                "job_title": known.get("job_title"),
                "site": known.get("site"),
                "department": known.get("department"),
            },
        )
    s = await load_module_settings(ctx.session)
    emp = _emp(ctx)
    try:
        async with await _sdp(ctx, Purpose.READ) as c:
            users = await c.find_users(s.sdp_user_employee_id_field, emp["id"])
    except Exception as exc:
        if isinstance(exc, PersonalCredentialMissing):
            raise  # the step waits for the person's own key (engine), it does not fail
        return StepOutcome("failed", f"Không tìm được user SDP: {_err(exc)}")
    users = [normalize.sdp_user(u, s.sdp_user_employee_id_field) for u in users]
    users = [u for u in users if u.get("employee_id") == emp["id"]] or users
    if len(users) > 1:
        return StepOutcome(
            "failed",
            f"{len(users)} user SDP cùng Mã NV {emp['id']}: {', '.join(u['name'] for u in users)}. "
            "Chọn đúng user trong Job (bản nháp) hoặc sửa trên SDP rồi chạy lại.",
        )
    if not users:
        return StepOutcome(
            "waiting",
            f"Chưa có user SDP mang Mã NV {emp['id']} – chờ tài khoản AD được tạo và đồng bộ vào SDP. "
            "Kiểm tra lại sau 15 phút.",
            retry_in_seconds=900,
        )
    u = users[0]
    return StepOutcome(
        "done",
        f"Tìm thấy user SDP {u['name']}{f' ({u["email"]})' if u.get('email') else ''}.",
        # Job title and work location for the handover / return record (SDP is the reference).
        {
            "sdp_user_id": u["id"],
            "sdp_user_name": u["name"],
            "job_title": u.get("job_title"),
            "site": u.get("site"),
            "department": u.get("department"),
        },
    )


async def preview_find_sdp_user(ctx: StepContext) -> list[str]:
    if ctx.params.get("sdp_user_id"):
        return [f"Dùng user SDP đã chọn: {ctx.params.get('sdp_user_name') or ctx.params['sdp_user_id']}."]
    return [f"Tìm user SDP mang Mã NV {_emp(ctx).get('id')}; nếu chưa có thì chờ (kiểm tra lại mỗi 15 phút)."]


async def _users_by_upn(session: AsyncSession, upn: str | None) -> str:
    if not upn:
        return ""
    clean = upn.split(" (")[-1].rstrip(")") if upn.startswith("worker (") else upn
    u = await session.scalar(select(User).where(User.upn == clean))
    return u.display_name if u else clean


async def _document_step(ctx: StepContext, kind_short: str) -> StepOutcome:
    """Create the record draft once, then wait until it is signed."""
    kind = HANDOVER if kind_short == "handover" else RETURN
    doc_id = (ctx.step.output or {}).get("document_id")
    if doc_id:
        doc = await documents.get_document(ctx.session, doc_id)
        if doc.status == "signed":
            return StepOutcome(
                "done", f"Biên bản {doc.number} đã ký.", {"document_id": doc_id, "number": doc.number}
            )
        if doc.status == "void":
            return StepOutcome(
                "failed",
                f"Biên bản {doc.number or '(nháp)'} đã bị huỷ. Chạy lại bước để lập biên bản mới.",
                {"document_id": None},
            )
        label = {
            "draft": "đang là bản nháp – kiểm tra rồi chốt",
            "finalized": f"đã chốt ({doc.number}) – in, ký và tải bản ký lên",
        }
        return StepOutcome(
            "waiting",
            f"Biên bản {label.get(doc.status, doc.status)}.",
            {"document_id": doc_id},
            retry_in_seconds=60,
        )
    s = await load_module_settings(ctx.session)
    emp = _emp(ctx)
    today = today_vn().isoformat()
    if kind_short == "handover":
        assets = [
            {"name": a["name"], "tag": a.get("tag") or "", "date": today, "note": "", "sdp_asset_id": a["id"]}
            for a in (ctx.outputs.get("assign_assets") or {}).get("assigned", [])
        ]
    else:
        assets = [
            {
                "name": a["name"],
                "tag": a.get("tag") or "",
                "date": today if a["condition"] != "not_returned" else None,
                "condition": a["condition"],
                "note": a.get("note") or "",
                "sdp_asset_id": a["id"],
            }
            for a in (ctx.outputs.get("collect_assets") or {}).get("returned", [])
        ]
    if not assets:
        return StepOutcome(
            "failed", "Không có thiết bị nào để lập biên bản. Bỏ qua bước này nếu không cần biên bản."
        )
    sdp = ctx.outputs.get("find_sdp_user") or {}
    data = {
        "employee": {
            "id": emp["id"],
            "full_name": emp.get("full_name") or emp["id"],
            "department": sdp.get("department") or emp.get("department") or "",
            "job_title": sdp.get("job_title") or emp.get("job_title") or "",
            "work_location": sdp.get("site") or emp.get("work_location") or "",
        },
        "date": today,
        "it_rep": await _users_by_upn(ctx.session, ctx.job.started_by) or "Infra Support",
        "reviewer": s.reviewer_name,
        "request_id": ctx.params.get("request_id"),
        "assets": assets,
    }
    doc = await documents.create_draft(ctx.session, ctx.actor, kind, MODULE_ID, data)
    return StepOutcome(
        "waiting",
        f"Đã lập bản nháp {kind.title.lower()}. Kiểm tra, chốt, in cho các bên ký và tải bản ký lên.",
        {"document_id": str(doc.id)},
        retry_in_seconds=60,
    )


async def close_request(ctx: StepContext) -> StepOutcome:
    """Attach the signed record, add a note and set the request status. Each part runs once (retry-safe)."""
    s = await load_module_settings(ctx.session)
    rid = ctx.params["request_id"]
    done = dict(ctx.step.output or {})
    doc_key = "handover_doc" if ctx.params.get("kind") == "onboard" else "return_doc"
    doc_id = (ctx.outputs.get(doc_key) or {}).get("document_id")
    try:
        async with await _sdp(ctx, Purpose.WRITE) as c:
            if doc_id and not done.get("attached"):
                doc = await documents.get_document(ctx.session, doc_id)
                if doc.signed_file_id:
                    f, data = await read_file(ctx.session, doc.signed_file_id)
                    # Read first: a timed-out upload may have landed; never attach the same record twice.
                    # Match on the record number (put in the uploaded name), never on a generic name like scan.pdf.
                    num = doc.number or str(doc.id)  # signed records are always numbered; id as a fallback
                    fname = f.filename if num in f.filename else f"{num}-{f.filename}"
                    names = [str(x.get("name") or "") for x in await c.request_attachments(rid)]
                    if not any(num in n for n in names):
                        await c.post_file(
                            s.sdp_attach_path.replace("{request_id}", rid),
                            s.sdp_attach_field,
                            fname,
                            data,
                            f.mime,
                        )
                        documents.add_event(
                            doc, {"type": "sdp_attach", "ok": True, "request_id": rid, "by": ctx.actor.upn}
                        )
                    done["attached"] = doc.number
            if s.sdp_add_note and not done.get("noted"):
                # Our note names infra-ops and the employee id; SDP may reformat the HTML, so match text.
                emp_id = f"({_emp(ctx).get('id')})"
                notes = [str(n.get("description") or "") for n in await c.request_notes(rid)]
                if not any("infra-ops" in n and emp_id in n for n in notes):
                    await c.add_note(rid, _summary_html(ctx))
                done["noted"] = True
            if not done.get("status_set"):
                await c.set_request_status(rid, s.sdp_close_status)
                done["status_set"] = s.sdp_close_status
    except Exception as exc:
        if isinstance(exc, PersonalCredentialMissing):
            raise  # the step waits for the person's own key (engine), it does not fail
        return StepOutcome("failed", f"SDP: {_err(exc)}", done)
    parts = [
        f"đính kèm {done['attached']}" if done.get("attached") else None,
        "thêm ghi chú" if done.get("noted") else None,
        f"chuyển trạng thái “{done['status_set']}”",
    ]
    return StepOutcome("done", "Đã cập nhật request SDP: " + ", ".join(p for p in parts if p) + ".", done)


KIND_TITLE = {"onboard": "Onboard", "offboard": "Offboard", "transfer": "Thuyên chuyển"}


def _summary_html(ctx: StepContext) -> str:
    lines = [
        f"<p><b>infra-ops</b> – {KIND_TITLE.get(ctx.params.get('kind') or '', 'Offboard')} "
        f"{html.escape(str(_emp(ctx).get('full_name')))} ({html.escape(str(_emp(ctx).get('id')))})</p><ul>"
    ]
    for s in sorted(ctx.job.steps, key=lambda s: s.seq):
        if s.key == ctx.step.key:
            continue
        mark = "✔" if s.status == "done" else ("–" if s.status == "skipped" else "•")
        lines.append(f"<li>{mark} {html.escape(s.title)}: {html.escape(s.message or '')}</li>")
    lines.append("</ul>")
    return "".join(lines)


async def preview_close(ctx: StepContext) -> list[str]:
    s = await load_module_settings(ctx.session)
    out = ["Đính kèm bản scan biên bản đã ký vào request."]
    if s.sdp_add_note:
        out.append("Thêm ghi chú tóm tắt kết quả các bước vào request.")
    out.append(f"Chuyển request sang trạng thái “{s.sdp_close_status}”.")
    return out


# ------------------------------------------------------------------ onboard
async def assign_assets(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    wanted = ctx.params.get("assets") or []
    if not wanted:
        return StepOutcome("done", "Job không có tài sản cần gán.", {"assigned": []})
    uid = _sdp_user_id(ctx)
    if not uid:
        return StepOutcome("failed", "Chưa xác định được user SDP để gán tài sản.")
    done = {a["id"]: a for a in (ctx.step.output or {}).get("assigned", [])}
    before = dict((ctx.step.before_state or {}).get("assets", {}))
    try:
        async with await _sdp(ctx, Purpose.WRITE) as c:
            for a in wanted:
                if a["id"] in done:
                    continue
                cur = normalize.sdp_asset(await c.get_asset(a["id"]))
                before.setdefault(
                    a["id"], {"state": cur["state"], "user_id": cur["user_id"], "user_name": cur["user_name"]}
                )
                if cur["user_id"] == uid and cur["state"] == s.asset_state_in_use:
                    done[a["id"]] = {**a, "note": "đã gán sẵn"}
                    continue
                if cur["state"] != s.asset_state_in_store or cur["user_id"]:
                    raise AppError(
                        f"Tài sản {cur['name']} đang ở trạng thái “{cur['state']}”"
                        f"{f' (gán cho {cur["user_name"]})' if cur['user_name'] else ''}, không phải “{s.asset_state_in_store}”. "
                        "Chọn tài sản khác trong Job hoặc xử lý trên SDP rồi chạy lại."
                    )
                await c.update_asset(a["id"], s.asset_state_in_use, uid)
                done[a["id"]] = a
    except Exception as exc:
        if isinstance(exc, PersonalCredentialMissing):
            raise  # the step waits for the person's own key (engine), it does not fail
        return StepOutcome("failed", _err(exc), {"assigned": list(done.values())}, {"assets": before})
    names = ", ".join(f"{a['name']} ({a.get('tag') or '—'})" for a in done.values())
    return StepOutcome(
        "done",
        f"Đã gán {len(done)} tài sản (In Use): {names}.",
        {"assigned": list(done.values())},
        {"assets": before},
    )


async def preview_assign(ctx: StepContext) -> list[str]:
    s = await load_module_settings(ctx.session)
    assets = ctx.params.get("assets") or []
    if not assets:
        return [
            "Chưa chọn tài sản – bước này sẽ không thay đổi gì. Chọn tài sản In Store trong bản nháp Job."
        ]
    who = ctx.params.get("sdp_user_name") or "user SDP tìm được ở bước trước"
    return [
        f"Đổi {a['name']} ({a.get('tag') or '—'}) từ “{s.asset_state_in_store}” sang “{s.asset_state_in_use}”, gán cho {who}."
        for a in assets
    ]


async def biostar_create(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    emp = _emp(ctx)
    start = ctx.params.get("effective_date") or today_vn().isoformat()
    payload = {
        "user_id": emp["id"],
        "name": emp.get("full_name") or emp["id"],
        "disabled": "false",
        "user_group_id": {"id": s.biostar_user_group_id},
        "start_datetime": f"{start}T00:00:00.00Z",
        "expiry_datetime": f"{s.biostar_expiry_date}T23:59:00.00Z",
        # Global list plus the groups of every matching department rule (same formula as transfer).
        "access_groups": [
            {"id": g} for g in dict.fromkeys([*s.biostar_access_group_ids, *tr.biostar_groups([], emp, s)[0]])
        ],
    }
    try:
        async with await _biostar(ctx, Purpose.WRITE) as c:
            existing = await c.get_user(emp["id"])
            if existing:
                if str(existing.get("disabled")).lower() == "true":
                    await c.update_user(
                        emp["id"], {"disabled": "false", "access_groups": payload["access_groups"]}
                    )
                    return StepOutcome(
                        "done",
                        f"User BioStar {emp['id']} đã có nhưng đang vô hiệu – đã kích hoạt lại.",
                        {"created": False, "reactivated": True},
                        {"user": existing},
                    )
                return StepOutcome(
                    "done",
                    f"User BioStar {emp['id']} đã tồn tại và đang hoạt động – không tạo lại.",
                    {"created": False},
                    {"user": existing},
                )
            await c.create_user(payload)
    except Exception as exc:
        return StepOutcome("failed", f"BioStar: {_err(exc)}")
    return StepOutcome(
        "done",
        f"Đã tạo user BioStar {emp['id']} – {payload['name']}.",
        {"created": True, "payload": payload},
        {"user": None},
    )


async def preview_biostar_create(ctx: StepContext) -> list[str]:
    s = await load_module_settings(ctx.session)
    emp = _emp(ctx)
    ids = dict.fromkeys([*s.biostar_access_group_ids, *tr.biostar_groups([], emp, s)[0]])
    groups = ", ".join(ids) or "chưa cấu hình (không gán)"
    return [
        f"Tạo user BioStar mã {emp.get('id')} – {emp.get('full_name')} (nếu chưa có).",
        f"Nhóm người dùng ID {s.biostar_user_group_id}; nhóm quyền ra vào ID: {groups}; "
        f"hiệu lực từ {_fmt(ctx.params.get('effective_date'))} đến {_fmt(s.biostar_expiry_date)}.",
    ]


async def biostar_enroll(ctx: StepContext) -> StepOutcome:
    """Manual: the person enrolled card / fingerprint / face at a device; we verify on BioStar."""
    emp = _emp(ctx)
    try:
        async with await _biostar(ctx, Purpose.READ) as c:
            user = await c.get_user(emp["id"])
    except Exception as exc:
        if ctx.input.get("force"):
            return StepOutcome(
                "done",
                f"Xác nhận thủ công (không kiểm tra được BioStar: {_err(exc)}). {ctx.input.get('note') or ''}".strip(),
            )
        raise AppError(
            f"Không đọc được BioStar để kiểm tra: {_err(exc)}. Chọn “Xác nhận dù không kiểm tra được” nếu đã đăng ký."
        ) from exc
    if user is None:
        raise AppError(f"Chưa có user BioStar mã {emp['id']}.")
    n = normalize.biostar_user(user)
    out = {"cards": n["cards"], "fingerprints": n["fingerprints"], "faces": n["faces"]}
    if n["cards"] + n["fingerprints"] + n["faces"] == 0 and not ctx.input.get("force"):
        raise AppError(
            "BioStar chưa ghi nhận thẻ, vân tay hay khuôn mặt nào. Đăng ký tại thiết bị rồi xác nhận lại."
        )
    return StepOutcome(
        "done", f"Đã đăng ký: {n['cards']} thẻ, {n['fingerprints']} vân tay, {n['faces']} khuôn mặt.", out
    )


async def handover_doc(ctx: StepContext) -> StepOutcome:
    return await _document_step(ctx, "handover")


async def return_doc(ctx: StepContext) -> StepOutcome:
    return await _document_step(ctx, "return")


async def preview_doc(ctx: StepContext) -> list[str]:
    title = "Biên bản Bàn giao" if ctx.params.get("kind") == "onboard" else "Biên bản Thu hồi"
    return [
        f"Tự lập bản nháp {title} từ các thiết bị của Job.",
        "Chờ Infra Support chốt, in, lấy chữ ký và tải bản đã ký lên.",
    ]


# ------------------------------------------------------------------ offboard
async def _snapshot_assets(session: AsyncSession, employee_id: str) -> list[dict[str, Any]]:
    run = await latest_run(session)
    if run is None:
        return []
    row = await session.scalar(
        select(ReconRow).where(ReconRow.run_id == run.id, ReconRow.employee_id == employee_id)
    )
    if row is None or not row.data.get("asset_ids"):
        return []
    snaps = (
        await session.scalars(
            select(Snapshot).where(
                Snapshot.run_id == run.id,
                Snapshot.system == "sdp_asset",
                Snapshot.external_id.in_(row.data["asset_ids"]),
            )
        )
    ).all()
    return [s.data for s in snaps]


async def prepare_collect(ctx: StepContext) -> StepOutcome:
    """List the assets currently assigned on SDP (live); fall back to the latest sync when SDP is unreachable."""
    uid = _sdp_user_id(ctx)
    assets: list[dict[str, Any]] = []
    source = "SDP"
    if uid:
        try:
            async with await _sdp(ctx, Purpose.READ) as c:
                assets = [normalize.sdp_asset(a) for a in await c.assets_of_user(uid)]
        except Exception:
            uid = None
    if not uid:
        source = "lần đồng bộ gần nhất"
        assets = await _snapshot_assets(ctx.session, _emp(ctx)["id"])
    listed = [
        {
            "id": a["id"],
            "name": a["name"],
            "tag": a.get("asset_tag"),
            "state": a.get("state"),
            "product_type": a.get("product_type"),
        }
        for a in assets
    ]
    return StepOutcome(
        "waiting",
        f"{len(listed)} tài sản đang gán (theo {source}). Xác nhận tình trạng từng tài sản khi thu hồi.",
        {"assets": listed, "source": source},
    )


async def collect_assets(ctx: StepContext) -> StepOutcome:
    """Manual completion: one condition per listed asset (+ extra assets added by hand)."""
    listed = {a["id"]: a for a in (ctx.step.output or {}).get("assets", [])}
    rows = ctx.input.get("assets") or []
    allowed = {"good", "damaged", "missing_accessory", "not_returned"}
    returned = []
    for r in rows:
        if r.get("condition") not in allowed:
            raise AppError("Chọn tình trạng cho mọi tài sản.")
        base = listed.get(r.get("id")) or {
            "id": r.get("id"),
            "name": r.get("name") or "",
            "tag": r.get("tag"),
        }
        if not base.get("name"):
            raise AppError("Tài sản thêm tay cần có tên.")
        returned.append({**base, "condition": r["condition"], "note": (r.get("note") or "")[:300]})
    missing = set(listed) - {r["id"] for r in returned}
    if missing:
        raise AppError(f"Còn {len(missing)} tài sản chưa xác nhận tình trạng.")
    lost = [r for r in returned if r["condition"] == "not_returned"]
    msg = f"Đã xác nhận {len(returned)} tài sản" + (f", {len(lost)} chưa thu hồi được" if lost else "") + "."
    return StepOutcome("done", msg, {"returned": returned})


async def return_assets(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    rows = [
        r
        for r in (ctx.outputs.get("collect_assets") or {}).get("returned", [])
        if r["condition"] != "not_returned" and r.get("id") and not str(r["id"]).startswith("manual-")
    ]
    if not rows:
        return StepOutcome("done", "Không có tài sản nào cần chuyển về kho.", {"returned_ids": []})
    done = set((ctx.step.output or {}).get("returned_ids", []))
    before = dict((ctx.step.before_state or {}).get("assets", {}))
    try:
        async with await _sdp(ctx, Purpose.WRITE) as c:
            for r in rows:
                if r["id"] in done:
                    continue
                cur = normalize.sdp_asset(await c.get_asset(r["id"]))
                before.setdefault(
                    r["id"], {"state": cur["state"], "user_id": cur["user_id"], "user_name": cur["user_name"]}
                )
                if cur["state"] != s.asset_state_in_store or cur["user_id"]:
                    await c.update_asset(r["id"], s.asset_state_in_store, None)
                done.add(r["id"])
    except Exception as exc:
        if isinstance(exc, PersonalCredentialMissing):
            raise  # the step waits for the person's own key (engine), it does not fail
        return StepOutcome("failed", _err(exc), {"returned_ids": sorted(done)}, {"assets": before})
    return StepOutcome(
        "done",
        f"Đã chuyển {len(done)} tài sản về “{s.asset_state_in_store}” và bỏ gán user.",
        {"returned_ids": sorted(done)},
        {"assets": before},
    )


async def preview_return(ctx: StepContext) -> list[str]:
    s = await load_module_settings(ctx.session)
    rows = (ctx.outputs.get("collect_assets") or {}).get("returned")
    if rows is None:
        return [
            f"Sau khi xác nhận thu hồi: chuyển các tài sản đã nhận lại về “{s.asset_state_in_store}” và bỏ gán user. "
            "Tài sản “Chưa thu hồi” giữ nguyên."
        ]
    return [
        f"{r['name']}: → “{s.asset_state_in_store}”"
        if r["condition"] != "not_returned"
        else f"{r['name']}: giữ nguyên (chưa thu hồi)"
        for r in rows
    ]


async def biostar_offboard(ctx: StepContext) -> StepOutcome:
    s = await load_module_settings(ctx.session)
    emp = _emp(ctx)
    try:
        async with await _biostar(ctx, Purpose.WRITE) as c:
            user = await c.get_user(emp["id"])
            if user is None:
                return StepOutcome(
                    "done", f"Không có user BioStar mã {emp['id']} – không cần xử lý.", {"action": "none"}
                )
            if s.biostar_offboard_action == "delete":
                await c.delete_user(emp["id"])
                msg = f"Đã xoá user BioStar {emp['id']}."
            else:
                await c.update_user(emp["id"], {"disabled": "true", "access_groups": []})
                msg = f"Đã vô hiệu hoá user BioStar {emp['id']} và gỡ khỏi mọi nhóm quyền ra vào."
    except Exception as exc:
        return StepOutcome("failed", f"BioStar: {_err(exc)}")
    return StepOutcome("done", msg, {"action": s.biostar_offboard_action}, {"user": user})


async def preview_biostar_offboard(ctx: StepContext) -> list[str]:
    s = await load_module_settings(ctx.session)
    emp = _emp(ctx)
    lines = [
        f"Xoá user BioStar {emp.get('id')}."
        if s.biostar_offboard_action == "delete"
        else f"Vô hiệu hoá user BioStar {emp.get('id')} và gỡ khỏi mọi nhóm quyền ra vào."
    ]
    try:
        async with await _biostar(ctx, Purpose.READ) as c:
            user = await c.get_user(emp["id"])
        if user:
            n = normalize.biostar_user(user)
            lines.append(
                f"Hiện tại: {'đang hoạt động' if n['active'] else 'không hoạt động'}, nhóm quyền: {', '.join(n['access_groups']) or 'không có'}."
            )
        else:
            lines.append("Hiện không có user BioStar mang mã này.")
    except Exception as exc:
        lines.append(f"(Không đọc được BioStar: {_err(exc)})")
    lines.append("Cần nhập lại Mã NV để xác nhận trước khi chạy.")
    return lines


# ------------------------------------------------------------------ transfer
async def prepare_groups(ctx: StepContext) -> StepOutcome:
    """Show where the person moves from (SDP user) and to (RTR request) for the manual group changes."""
    old = (ctx.outputs.get("find_sdp_user") or {}).get("department") or "chưa rõ (xem user SDP / AD)"
    new = _emp(ctx).get("department") or "chưa rõ (xem request RTR)"
    return StepOutcome("waiting", f"Từ: {old} → Đến: {new}.", {"from": old, "to": new})


async def biostar_transfer(ctx: StepContext) -> StepOutcome:
    """On the effective date replay the pinned access-group lines: remove only pinned ids the person still has and
    the settings still manage, add the pinned ones; the whole list is PUT (BioStar replaces)."""
    s = await load_module_settings(ctx.session)
    plan = pinned_transfer(ctx)
    lines = plan.get("biostar") or {}
    if not (lines.get("add") or lines.get("remove")):
        return StepOutcome("skipped", "Kế hoạch không có thay đổi nhóm quyền ra vào BioStar – giữ nguyên.")
    if wait := effective_wait(plan):
        return wait
    eid = str(plan.get("employee_id") or _emp(ctx).get("id") or "")
    try:
        async with await _biostar(ctx, Purpose.WRITE) as c:
            user = await c.get_user(eid)
            if user is None or str(user.get("disabled")).lower() == "true":
                return StepOutcome("skipped", f"Không có user BioStar đang hoạt động mã {eid} – bỏ qua.")
            if not isinstance(user.get("access_groups"), list):  # never PUT a list built on a missing read
                return StepOutcome("failed", f"BioStar không trả danh sách nhóm quyền của {eid} – không ghi.")
            before = [str(g["id"]) for g in user["access_groups"] if isinstance(g, dict) and "id" in g]
            drop = [i for i in before if i in lines.get("remove", []) and i in tr.managed_biostar(s)]
            add = [i for i in lines.get("add", []) if i not in before]
            new = [*(i for i in before if i not in drop), *add]
            changed = bool(drop or add)
            if changed:  # unchanged = no write (idempotent on retry)
                await c.update_user(eid, {"access_groups": [{"id": i} for i in new]})
    except Exception as exc:
        return StepOutcome("failed", f"BioStar: {_err(exc)}")
    out = {"changed": changed, "added": add, "removed": drop}
    msg = "Đã cập nhật nhóm quyền ra vào." if changed else "Nhóm quyền ra vào đã đúng – không thay đổi."
    return StepOutcome("done", msg, out, {"access_group_ids": before})


async def preview_biostar_transfer(ctx: StepContext) -> list[str]:
    b = (ctx.outputs.get("transfer_plan") or {}).get("biostar") or {}
    return [
        f"Đọc user BioStar mã {_emp(ctx).get('id')}; thêm nhóm quyền {b.get('add') or '—'}, gỡ {b.get('remove') or '—'} "
        "(chỉ nhóm do quy tắc quản lý) – chỉ ghi khi có thay đổi, từ ngày hiệu lực."
    ]


async def prepare_transfer_assets(ctx: StepContext) -> StepOutcome:
    """Compare the devices in use with the standard of the new level: what to add (buy) and what exceeds it."""
    s = await load_module_settings(ctx.session)
    emp = _emp(ctx)
    have = list((await prepare_collect(ctx)).output["assets"])
    org = (ctx.outputs.get("transfer_plan") or {}).get("org") or {}  # the pinned post-transfer org
    items, level, _ = s.items_for_employee(
        emp.get("job_title"), None, org.get("department") or emp.get("department_only")
    )
    needs: list[dict[str, Any]] = []
    for item in items or []:
        taken = [a for a in have if matches(a, item)][: item.quantity]
        have = [a for a in have if a not in taken]
        if item.quantity > len(taken):
            needs.append(
                {
                    "label": item.label,
                    "quantity": item.quantity - len(taken),
                    "catalog_name": item.catalog_name,
                    "product_types": item.product_types,
                }
            )
    covered = {t.casefold() for i in items or [] for t in i.product_types}
    excess = [a for a in have if str(a.get("product_type") or "").casefold() in covered]
    out = {"level": level, "needs": needs, "excess": excess}
    msg = (
        f"Cần bổ sung {len(needs)} loại, {len(excess)} thiết bị vượt chuẩn."
        if items
        else "Chưa xác định cấp bậc."
    )
    return StepOutcome("waiting", msg, out)


async def run_transfer_assets(ctx: StepContext) -> StepOutcome:
    out = dict(ctx.step.output or {})
    if (out.get("needs") or out.get("excess")) and not ctx.input.get("acknowledged"):
        raise AppError("Xác nhận đã bổ sung / xem xét thu hồi thiết bị.", code="assets_unconfirmed")
    return StepOutcome("done", "Đã xử lý thiết bị theo cấp mới.", out)


def _microsoft(params: dict[str, Any]) -> bool:
    return bool(params.get("microsoft"))


def _not_microsoft(params: dict[str, Any]) -> bool:
    return not _microsoft(params)


CHECK = StepDefinition(
    "check_request",
    "Kiểm tra request SDP",
    "auto",
    "Đọc request trên SDP, dừng nếu request đã đóng.",
    run=check_request,
    preview=preview_check_request,
    skippable=False,
    checklist=("Request đúng nhân viên và loại biến động", "Ngày hiệu lực khớp với HR"),
)
FIND_USER = StepDefinition(
    "find_sdp_user",
    "Xác định user SDP",
    "auto",
    "Tìm user SDP (đồng bộ từ AD) theo Mã NV để gán/thu hồi tài sản.",
    run=find_sdp_user,
    preview=preview_find_sdp_user,
    wait_timeout_seconds=72 * 3600,
    checklist=("User SDP đúng Mã NV, họ tên và phòng ban",),
)
CLOSE = StepDefinition(
    "close_request",
    "Cập nhật và đóng request SDP",
    "auto",
    "Đính kèm biên bản đã ký, ghi chú kết quả, chuyển trạng thái request.",
    run=close_request,
    preview=preview_close,
    writes=("sdp",),
    checklist=("Request SDP đã đóng, có ghi chú kết quả và biên bản đính kèm",),
)

ONBOARD = WorkflowDefinition(
    key="lifecycle.onboard",
    title="Onboard nhân sự",
    view_permission=VIEW,
    view_all_permission=VIEW_ALL,
    execute_permission=EXECUTE,
    override_permission=OVERRIDE,
    required_for_close=("handover_doc",),
    verify_steps=True,
    steps=[
        CHECK,
        StepDefinition(
            "account_plan",
            "Kế hoạch tài khoản (username / OU)",
            "manual",
            "Chốt username, UPN và OU của tài khoản AD / email theo chức danh.",
            run=run_account_plan,
            when=_microsoft,
            prepare=prepare_account_plan,
            skippable=False,
            checklist=("Chức danh và hàng ma trận đúng", "Username/UPN/OU đúng quy tắc"),
        ),
        StepDefinition(
            "ad_account",
            "Tạo tài khoản AD và remote mailbox",
            "auto",
            "Tạo user AD (employeeID = Mã NV) theo kế hoạch đã chốt và bật remote mailbox.",
            run=ad_account,
            when=_microsoft,
            preview=preview_ad_account,
            writes=("ad", "exchange"),
            confirm=True,
            confirm_text="Sẽ tạo tài khoản AD và mailbox. Nhập lại Mã NV để xác nhận.",
            skippable=False,
            checklist=("Tài khoản AD đã tạo đúng OU", "Remote mailbox đã bật"),
        ),
        StepDefinition(
            "m365_license",
            "Gán license Microsoft 365 và chờ mailbox",
            "auto",
            "Gán license theo chức danh sau khi user được đồng bộ lên Entra; chờ mailbox Exchange Online.",
            run=m365_license,
            when=_microsoft,
            preview=preview_m365_license,
            writes=("m365",),
            wait_timeout_seconds=M365_WAIT_SECONDS,
            checklist=("License đúng theo chức danh", "Mailbox Exchange Online đã sẵn sàng"),
        ),
        FIND_USER,
        StepDefinition(
            "assign_assets",
            "Gán tài sản (In Store → In Use)",
            "auto",
            "Gán các tài sản đã chọn cho nhân viên trên SDP.",
            run=assign_assets,
            preview=preview_assign,
            writes=("sdp",),
            checklist=(
                "Tài sản trên SDP đã In Use và gán đúng nhân viên",
                "Asset Tag khớp với thiết bị bàn giao thực tế",
            ),
            evidence_required=True,
        ),
        StepDefinition(
            "biostar_user",
            "Tạo user BioStar",
            "auto",
            "Tạo user ra vào với user_id = Mã NV, nhóm và nhóm quyền ra vào mặc định.",
            run=biostar_create,
            preview=preview_biostar_create,
            writes=("biostar",),
            checklist=("User BioStar đúng Mã NV và họ tên", "Đúng nhóm người dùng và nhóm quyền ra vào"),
            evidence_required=True,
        ),
        StepDefinition(
            "biostar_enroll",
            "Đăng ký thẻ / vân tay / khuôn mặt",
            "manual",
            "Nhân viên đăng ký tại thiết bị BioStation 3; infra-ops kiểm tra lại trên BioStar khi xác nhận.",
            run=biostar_enroll,
            checklist=("Nhân viên đã đăng ký thẻ / vân tay / khuôn mặt", "Thử ra vào cửa thành công"),
            evidence_required=True,
        ),
        StepDefinition(
            "it_handover",
            "Bàn giao máy và hướng dẫn",
            "manual",
            "Cài phần mềm theo chức danh, đặt mật khẩu tạm trên ADUC (không nhập vào hệ thống này), hướng dẫn đăng nhập.",
            run=run_it_handover,
            when=_microsoft,
            prepare=prepare_it_handover,
            skippable=False,
            checklist=(
                "Đã cài bộ Office tools theo danh sách",
                "Đã đặt mật khẩu tạm trên ADUC và yêu cầu đổi khi đăng nhập",
                "Đã hướng dẫn đăng nhập email / Teams",
            ),
        ),
        StepDefinition(
            "handover_doc",
            "Biên bản Bàn giao (đã ký)",
            "auto",
            "Lập biên bản từ tài sản đã gán; chờ chốt, ký và tải bản ký lên.",
            run=handover_doc,
            preview=preview_doc,
            skippable=False,
            checklist=("Biên bản đủ chữ ký các bên", "Thông tin thiết bị trên biên bản khớp thực tế"),
        ),
        StepDefinition(
            "rtr_checklist",
            "Cập nhật RTR",
            "manual",
            "Sao chép tóm tắt vào hệ thống RTR để HR tick hoàn tất các task.",
            run=run_rtr_checklist,
            when=_microsoft,
            prepare=prepare_rtr_checklist,
            skippable=False,
            checklist=("Đã tick hoàn tất các task trên hệ thống RTR",),
        ),
        CLOSE,
    ],
)

OFFBOARD = WorkflowDefinition(
    key="lifecycle.offboard",
    title="Offboard nhân sự",
    view_permission=VIEW,
    view_all_permission=VIEW_ALL,
    execute_permission=EXECUTE,
    override_permission=OVERRIDE,
    required_for_close=("return_doc",),
    verify_steps=True,
    steps=[
        CHECK,
        StepDefinition(
            "biostar_offboard",
            "Vô hiệu hoá / xoá user BioStar",
            "auto",
            "Theo cấu hình module; cần nhập lại Mã NV để xác nhận.",
            run=biostar_offboard,
            preview=preview_biostar_offboard,
            writes=("biostar",),
            confirm=True,
            confirm_text="Nhân viên sẽ không vào được văn phòng bằng thẻ/vân tay/khuôn mặt. Nhập lại Mã NV để xác nhận.",
            checklist=("User BioStar đã vô hiệu hoá / xoá", "Thẻ / vân tay không còn mở được cửa"),
            evidence_required=True,
        ),
        StepDefinition(
            "find_sdp_user",
            "Xác định user SDP",
            "auto",
            "Tìm user SDP theo Mã NV để lấy danh sách tài sản đang gán.",
            run=find_sdp_user,
            preview=preview_find_sdp_user,
            wait_timeout_seconds=3600,
            checklist=("User SDP đúng Mã NV, họ tên và phòng ban",),
        ),
        # Access is revoked first (door, then AD / Entra): it must not wait for the devices to come back.
        StepDefinition(
            "account_disable",
            "Vô hiệu tài khoản AD và Entra",
            "auto",
            "Thu hồi phiên Entra, vô hiệu tài khoản AD, chờ Entra ID báo đã vô hiệu rồi thu hồi phiên lần nữa.",
            run=account_disable,
            when=_microsoft,
            preview=preview_account_disable,
            writes=("m365", "ad"),
            confirm=True,
            confirm_text="Tài khoản AD / email sẽ bị vô hiệu ngay. Nhập lại Mã NV để xác nhận.",
            skippable=False,
            wait_timeout_seconds=3 * 3600,
            checklist=("Tài khoản đã bị vô hiệu trên AD và Entra",),
        ),
        StepDefinition(
            "mailbox_handover",
            "Chuyển mailbox (nếu có yêu cầu)",
            "manual",
            "Chỉ khi quản lý yêu cầu: chuyển mailbox sang shared hoặc forward. On-prem: Set-RemoteMailbox -Type Shared; "
            "Exchange Online: Set-Mailbox -Type Shared. Không yêu cầu thì bỏ qua bước.",
            run=run_mailbox_handover,
            when=_microsoft,
            prepare=prepare_mailbox_handover,
            checklist=("Đã chuyển mailbox sang shared / forward theo yêu cầu của quản lý",),
            evidence_required=True,
        ),
        StepDefinition(
            "ad_move_ban",
            "Chuyển tài khoản vào Ban_Users",
            "auto",
            "Chuyển tài khoản AD vào OU Ban_Users và chờ user bị xoá khỏi Entra ID (license được thu hồi). "
            "Nếu có yêu cầu giữ mailbox: chuyển vào OU giữ mailbox và lên lịch Job chuyển Ban_Users khi hết hạn.",
            run=ad_move_ban,
            when=_microsoft,
            preview=preview_ad_move_ban,
            writes=("ad",),
            confirm=True,
            confirm_text=(
                "Chuyển vào Ban_Users: Entra sẽ xoá user, mailbox và license (khôi phục được trong 30 ngày). "
                "Nếu có yêu cầu giữ mailbox, tài khoản vào OU giữ mailbox và chỉ chuyển Ban_Users khi hết hạn. "
                "Nhập lại Mã NV để xác nhận."
            ),
            skippable=False,
            wait_timeout_seconds=3 * 3600,
            checklist=("Tài khoản đã chuyển vào Ban_Users",),
        ),
        StepDefinition(
            "collect_assets",
            "Thu hồi tài sản (xác nhận tình trạng)",
            "manual",
            "Infra Support nhận lại thiết bị và chọn tình trạng: Tốt / Hỏng / Thiếu phụ kiện / Chưa thu hồi.",
            run=collect_assets,
            prepare=prepare_collect,
            skippable=False,
            checklist=("Đã nhận lại thiết bị theo danh sách", "Tình trạng từng thiết bị đã ghi nhận đúng"),
            evidence_required=True,
        ),
        StepDefinition(
            "return_assets",
            "Chuyển tài sản về kho (In Store)",
            "auto",
            "Bỏ gán user và chuyển tài sản đã nhận lại về In Store trên SDP.",
            run=return_assets,
            preview=preview_return,
            writes=("sdp",),
            checklist=("Tài sản đã về In Store và bỏ gán user trên SDP",),
        ),
        StepDefinition(
            "return_doc",
            "Biên bản Thu hồi (đã ký)",
            "auto",
            "Lập biên bản từ kết quả thu hồi; chờ chốt, ký và tải bản ký lên.",
            run=return_doc,
            preview=preview_doc,
            skippable=False,
            checklist=("Biên bản đủ chữ ký các bên", "Tình trạng thiết bị trên biên bản khớp thực tế"),
        ),
        StepDefinition(
            "rtr_checklist",
            "Cập nhật RTR",
            "manual",
            "Sao chép tóm tắt vào hệ thống RTR để HR tick hoàn tất các task.",
            run=run_rtr_checklist,
            when=_microsoft,
            prepare=prepare_rtr_checklist,
            skippable=False,
            checklist=("Đã tick hoàn tất các task trên hệ thống RTR",),
        ),
        CLOSE,
    ],
)

# Created by the offboard job when a mailbox is kept: runs on the retention end date.
MAILBOX_RELEASE = WorkflowDefinition(
    key="lifecycle.mailbox_release",
    title="Hết hạn giữ mailbox – chuyển Ban_Users",
    view_permission=VIEW,
    view_all_permission=VIEW_ALL,
    execute_permission=EXECUTE,
    override_permission=OVERRIDE,
    verify_steps=True,
    steps=[
        StepDefinition(
            "ad_move_ban",
            "Chuyển tài khoản vào Ban_Users",
            "auto",
            "Hết hạn giữ mailbox: chuyển tài khoản AD vào OU Ban_Users và chờ user bị xoá khỏi Entra ID.",
            run=ad_move_ban_final,
            when=_microsoft,
            preview=preview_ad_move_ban_final,
            writes=("ad",),
            confirm=True,
            confirm_text=(
                "Chuyển vào Ban_Users: Entra sẽ xoá user, mailbox và license (khôi phục được trong 30 ngày). "
                "Nhập lại Mã NV để xác nhận."
            ),
            skippable=False,
            wait_timeout_seconds=3 * 3600,
            checklist=("Tài khoản đã chuyển vào Ban_Users", "Mailbox không còn cần giữ"),
        ),
    ],
)

# Change of department or direct manager: IT moves the person between AD groups by hand (shared folders, mail
# groups); transfer is not automated yet (Phase 69 covers onboard/offboard only), so these are verified manual steps.
TRANSFER = WorkflowDefinition(
    key="lifecycle.transfer",
    title="Thuyên chuyển / đổi cấp trên",
    view_permission=VIEW,
    view_all_permission=VIEW_ALL,
    execute_permission=EXECUTE,
    override_permission=OVERRIDE,
    required_for_close=("groups_add",),
    verify_steps=True,
    steps=[
        CHECK,
        FIND_USER,
        StepDefinition(
            "transfer_plan",
            "Kế hoạch thuyên chuyển (OU / nhóm / thuộc tính)",
            "manual",
            "Kiểm tra thay đổi OU, nhóm AD và thuộc tính theo request; bỏ chọn nhóm không gỡ.",
            run=run_transfer_plan,
            when=_microsoft,
            prepare=prepare_transfer_plan,
            skippable=False,
            checklist=("OU, nhóm cần thêm/gỡ và thuộc tính đúng theo request",),
        ),
        StepDefinition(
            "ad_transfer",
            "Cập nhật AD (thuộc tính, OU, nhóm)",
            "auto",
            "Đến ngày hiệu lực: thêm nhóm mới, cập nhật thuộc tính, chuyển OU rồi gỡ nhóm cũ.",
            run=ad_transfer,
            when=_microsoft,
            preview=preview_ad_transfer,
            writes=("ad",),
            confirm=True,
            confirm_text="Sẽ thêm/gỡ nhóm, đổi thuộc tính và OU của tài khoản AD. Nhập lại Mã NV để xác nhận.",
            skippable=False,
            checklist=("Thuộc tính, OU và nhóm AD đúng theo kế hoạch",),
        ),
        StepDefinition(
            "m365_relicense",
            "Đổi license Microsoft 365 theo chức danh mới",
            "auto",
            "Đến ngày hiệu lực: thêm license của chức danh mới và gỡ license cũ trong một lần gọi.",
            run=m365_relicense,
            when=_microsoft,
            preview=preview_m365_relicense,
            writes=("m365",),
            confirm=True,
            confirm_text="Sẽ thêm/gỡ license Microsoft 365 của user. Nhập lại Mã NV để xác nhận.",
            wait_timeout_seconds=M365_WAIT_SECONDS,
            checklist=("License M365 đúng theo chức danh mới",),
        ),
        # Only in Microsoft jobs: a job with Microsoft off keeps exactly the old manual flow (no BioStar step).
        StepDefinition(
            "biostar_transfer",
            "Đổi nhóm quyền ra vào BioStar",
            "auto",
            "Đến ngày hiệu lực: đặt nhóm quyền ra vào theo phòng ban mới.",
            run=biostar_transfer,
            when=_microsoft,
            preview=preview_biostar_transfer,
            writes=("biostar",),
            confirm=True,
            confirm_text="Sẽ thêm/gỡ nhóm quyền ra vào BioStar của user. Nhập lại Mã NV để xác nhận.",
            checklist=("Nhóm quyền ra vào đúng phòng ban mới",),
        ),
        StepDefinition(
            "transfer_assets",
            "Thiết bị theo cấp bậc mới",
            "manual",
            "So thiết bị đang dùng với tiêu chuẩn cấp mới: bổ sung hoặc cân nhắc thu hồi phần vượt chuẩn.",
            run=run_transfer_assets,
            when=_microsoft,
            prepare=prepare_transfer_assets,
            checklist=("Đã bổ sung / thu hồi thiết bị theo cấp mới",),
        ),
        StepDefinition(
            "groups_remove",
            "Gỡ khỏi nhóm cũ",
            "manual",
            "Gỡ user khỏi nhóm AD của phòng/bộ phận cũ: quyền thư mục share và nhóm email.",
            prepare=prepare_groups,
            when=_not_microsoft,
            checklist=(
                "Đã gỡ quyền thư mục share của phòng/bộ phận cũ",
                "Đã gỡ khỏi nhóm email của phòng/bộ phận cũ",
            ),
            evidence_required=True,
        ),
        StepDefinition(
            "groups_add",
            "Thêm vào nhóm mới",
            "manual",
            "Thêm user vào nhóm AD theo phòng/bộ phận mới trong request RTR: quyền thư mục share và nhóm email.",
            prepare=prepare_groups,
            when=_not_microsoft,
            skippable=False,
            checklist=(
                "Đã cấp quyền thư mục share của phòng/bộ phận mới",
                "Đã thêm vào nhóm email của phòng/bộ phận mới",
                "Nhân viên truy cập được thư mục share mới",
            ),
            evidence_required=True,
        ),
        StepDefinition(
            "rtr_checklist",
            "Cập nhật RTR",
            "manual",
            "Sao chép tóm tắt vào hệ thống RTR để HR tick hoàn tất các task.",
            run=run_rtr_checklist,
            when=_microsoft,
            prepare=prepare_rtr_checklist,
            skippable=False,
            checklist=("Đã tick hoàn tất các task trên hệ thống RTR",),
        ),
        CLOSE,
    ],
)

WORKFLOW_BY_KIND = {"onboard": ONBOARD, "offboard": OFFBOARD, "transfer": TRANSFER}
