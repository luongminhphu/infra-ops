"""Workload module: SDP ticket sync, operator scoping, reassignment write-back, statistics."""

from __future__ import annotations

import io
import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.workflow.engine import pump
from app.modules.workload.rules import device_need, normalize_ticket, pick_bucket, ticket_phase, ticket_state
from app.modules.workload.settings import WorkloadSettings

SDP = "https://sdp-wl.test.local"
OK = {"response_status": {"status_code": 2000, "status": "success"}}


def ms(month: int, day: int, year: int = 2026) -> dict[str, str]:
    return {"value": str(int(datetime(year, month, day, 3, tzinfo=UTC).timestamp() * 1000))}


def close_body(po: dict) -> dict:  # type: ignore[type-arg]
    """What PoTab sends: the total and the items it displays."""
    return {"version_total": po["total"], "item_ids": [i["id"] for i in po["items"]]}


def req(
    rid: str, status: str, tech: str | None, created: int, due: dict | None, done: int | None = None
) -> dict:  # type: ignore[type-arg]
    r = {
        "id": rid,
        "display_id": f"D{rid}",
        "subject": f"Hỗ trợ {rid}",
        "status": {"name": status},
        "technician": {"id": tech, "name": f"Tech {tech}"} if tech else None,
        "requester": {"name": "Nguyễn Văn Khách"},
        "group": {"name": "Helpdesk"},
        "created_time": ms(11, created),
        "due_by_time": due,
        "last_updated_time": ms(11, 20),
    }
    if done:
        r["completed_time"] = ms(11, done)
    return r


# ------------------------------------------------------------------ pure rules
def test_normalize_rules() -> None:
    s = WorkloadSettings()
    t = normalize_ticket(req("1", "resolved", "7", 1, ms(11, 5), 4), s)
    assert t["state"] == "done" and t["technician_id"] == "7" and t["completed_at"] is not None
    # Re-opened tickets drop a stale completion time.
    reopened = normalize_ticket(req("3", "Open", None, 1, ms(11, 2), 6), s)
    assert reopened["state"] == "open" and reopened["completed_at"] is None
    assert normalize_ticket(req("4", "Canceled", None, 1, None), s)["state"] == "cancelled"
    # SDP in Vietnamese: a closed case is done even with a configuration saved before these names existed.
    old = WorkloadSettings(done_statuses=["Resolved"], cancelled_statuses=["Canceled"])
    assert ticket_state(" Đã xử lý ", old) == "done" and ticket_state("Đã đóng", old) == "done"
    assert ticket_state("Đã huỷ", old) == "cancelled" and ticket_state("Đang xử lý", old) == "open"
    assert [pick_bucket(d) for d in (7, 31, 92, 184, 365)] == ["day", "day", "week", "week", "month"]


# ------------------------------------------------------------------ API + worker
class FakeSdp:
    def __init__(self) -> None:
        far = ms(1, 1, 2099)
        self.requests = {
            "501": req("501", "Open", "11", 2, ms(11, 3)),
            "502": req("502", "Resolved", "11", 1, ms(11, 5), 4),
            "503": req("503", "Closed", "12", 1, ms(11, 2), 6),
            "504": req("504", "Open", None, 10, far),
            "505": req("505", "Canceled", "12", 3, None),
        }
        self.puts: list[str] = []
        self.assets: list[dict] = [{"id": "900", "serial_number": "SN-OLD", "name": "OLD"}]
        self.asset_posts: list[dict] = []
        self.fail_assets = False
        self.deny_techs = False
        self.techs = [
            {"id": "11", "name": "Tech 11", "email_id": "wl-op1@test.local"},
            {"id": "12", "name": "Tech 12", "email_id": "wl-op2@test.local"},
        ]

    def install(self, r: respx.MockRouter) -> None:
        denied = {
            "response_status": {
                "status_code": 4000,
                "status": "failed",
                "messages": [{"status_code": 4000, "message": "User does not have this permission"}],
            }
        }
        r.get(f"{SDP}/api/v3/technicians").mock(
            side_effect=lambda _: (
                httpx.Response(403, json=denied)
                if self.deny_techs
                else httpx.Response(
                    200, json={**OK, "list_info": {"has_more_rows": False}, "technicians": self.techs}
                )
            )
        )
        r.get(f"{SDP}/api/v3/requests").mock(
            side_effect=lambda _: httpx.Response(
                200,
                json={**OK, "list_info": {"has_more_rows": False}, "requests": list(self.requests.values())},
            )
        )

        def detail(request: httpx.Request, rid: str) -> httpx.Response:
            if request.method == "PUT":
                body = json.loads(parse_qs(request.content.decode())["input_data"][0])["request"]
                tid = body["technician"]["id"]
                self.requests[rid]["technician"] = {"id": tid, "name": f"Tech {tid}"}
                self.puts.append(f"{rid}->{tid}")
            return httpx.Response(200, json={**OK, "request": self.requests[rid]})

        r.route(url__regex=rf"{re.escape(SDP)}/api/v3/requests/(?P<rid>\d+)$").mock(side_effect=detail)

        def assets(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                if self.fail_assets:
                    return httpx.Response(
                        500, json={"response_status": {"status_code": 4000, "status": "failed"}}
                    )
                body = json.loads(parse_qs(request.content.decode())["input_data"][0])["asset"]
                created = {**body, "id": str(1000 + len(self.asset_posts))}
                self.asset_posts.append(body)
                self.assets.append(created)
                return httpx.Response(201, json={**OK, "asset": created})
            crit = json.loads(request.url.params["input_data"])["list_info"]["search_criteria"]
            crit = crit[0] if isinstance(crit, list) else crit
            rows = [a for a in self.assets if a.get(crit["field"]) == crit["value"]]
            return httpx.Response(200, json={**OK, "list_info": {"has_more_rows": False}, "assets": rows})

        r.route(url__regex=rf"{re.escape(SDP)}/api/v3/assets(\?.*)?$").mock(side_effect=assets)


async def _user(admin, role: str, upn: str) -> None:  # type: ignore[no-untyped-def]
    roles = {x["code"]: x["id"] for x in (await admin.get("/api/core/roles")).json()}
    users = {u["upn"]: u for u in (await admin.get("/api/core/users")).json()}
    if upn not in users:
        resp = await admin.post(
            "/api/core/users", json={"upn": upn, "display_name": upn, "role_ids": [roles[role]]}
        )
        assert resp.status_code == 201, resp.text


@pytest.fixture
async def sdp(admin, respx_mock):  # type: ignore[no-untyped-def]
    fake = FakeSdp()
    fake.install(respx_mock)
    existing = {p["name"]: p for p in (await admin.get("/api/core/connections")).json()}
    prof = (
        existing.get("SDP WL")
        or (
            await admin.post(
                "/api/core/connections",
                json={"system_type": "sdp", "name": "SDP WL", "base_url": SDP, "config": {}},
            )
        ).json()
    )
    for purpose in ("read", "write"):
        await admin.put(
            f"/api/core/connections/{prof['id']}/credentials/{purpose}",
            json={"auth_type": "api_key", "secrets": {"api_key": f"k-{purpose}-00000000"}},
        )
    values = (await admin.get("/api/workload/settings")).json()["values"]
    assert (
        await admin.put("/api/workload/settings", json={**values, "sdp_profile_id": prof["id"]})
    ).status_code == 200
    await _user(admin, "manager", "wl-mgr@test.local")
    await _user(admin, "operator", "wl-op1@test.local")
    await _user(admin, "operator", "wl-op2@test.local")
    await _user(admin, "viewer", "wl-view@test.local")
    await _user(admin, "supervisor", "wl-sup@test.local")
    from app.modules.workload.sync import run_sync

    async with get_sessionmaker()() as s:
        assert await run_sync(s) == {"technicians": 2, "tickets": 5}
    from tests.personal_keys import give_personal_keys

    await give_personal_keys(prof["id"])  # reassign / receive run with the person's own SDP key
    return fake


@respx.mock(assert_all_called=False)
async def test_sync_without_technician_list_permission(sdp) -> None:  # type: ignore[no-untyped-def]
    from app.modules.workload.models import Technician
    from app.modules.workload.sync import run_sync

    sdp.deny_techs = True  # read key without the SDP "technicians" admin permission
    async with get_sessionmaker()() as s:
        stats = await run_sync(s)
        # Technicians come from the requests instead; nobody is deactivated for missing from them.
        assert stats["technicians"] == 2 and stats["tickets"] == 5
        assert {t.id for t in (await s.scalars(select(Technician))).all() if t.active} == {"11", "12"}


@respx.mock(assert_all_called=False)
async def test_sync_operator_scope_and_stats(admin, client, sdp) -> None:  # type: ignore[no-untyped-def]
    techs = (await admin.get("/api/workload/technicians")).json()
    by_id = {t["id"]: t for t in techs["items"]}
    # Linked automatically by e-mail = UPN.
    assert by_id["11"]["user"]["upn"] == "wl-op1@test.local"
    assert (by_id["11"]["open"], by_id["11"]["overdue"]) == (1, 1)
    assert techs["unassigned"]["open"] == 1

    await client.login("wl-op1@test.local")
    mine = (await client.get("/api/workload/tickets?technician_id=me&state=all")).json()
    assert sorted(t["id"] for t in mine["items"]) == ["501", "502"]
    overdue = (await client.get("/api/workload/tickets?state=overdue")).json()
    assert [t["id"] for t in overdue["items"]] == ["501"]

    own = (await client.get("/api/workload/stats?from=2026-11-01&to=2026-11-30")).json()
    assert own["totals"]["received"] == 2  # operators see only their own figures
    viewer = await type(client)(transport=client._transport, base_url="http://test").login(
        "wl-view@test.local"
    )
    st = (await viewer.get("/api/workload/stats?from=2026-11-01&to=2026-11-30")).json()
    assert st["bucket"] == "day"
    assert st["totals"]["received"] == 5
    assert (st["totals"]["completed"], st["totals"]["completed_late"], st["totals"]["open"]) == (2, 1, 2)
    t11 = next(t for t in st["technicians"] if t["technician_id"] == "11")
    assert (t11["received"], t11["completed"], t11["on_time_rate"], t11["overdue"]) == (2, 1, 1.0, 1)
    assert sum(p["received"] for p in st["series"]) == 5
    only12 = (await viewer.get("/api/workload/stats?from=2026-11-01&to=2026-11-30&technician_id=12")).json()
    assert only12["totals"]["received"] == 2
    assert (await client.get("/api/workload/stats?from=2026-11-30&to=2026-11-01")).status_code == 400
    await viewer.aclose()

    # Operators can sync but not reassign or change settings.
    assert (await client.post("/api/workload/sync")).status_code == 200
    resp = await client.post(
        "/api/workload/tickets/reassign", json={"ticket_ids": ["501"], "to_technician_id": "12"}
    )
    assert resp.status_code == 403
    assert (await client.get("/api/workload/settings")).status_code == 403
    await client.login("wl-view@test.local")
    assert (await client.get("/api/workload/tickets")).status_code == 200
    assert (await client.post("/api/workload/sync")).status_code == 403


@respx.mock(assert_all_called=False)
async def test_manager_reassign_writes_back_to_sdp(admin, client, sdp) -> None:  # type: ignore[no-untyped-def]
    await client.login("wl-mgr@test.local")
    bad = await client.post(
        "/api/workload/tickets/reassign", json={"ticket_ids": ["503"], "to_technician_id": "12"}
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "ticket_not_open"

    resp = await client.post(
        "/api/workload/tickets/reassign",
        json={"ticket_ids": ["501", "504"], "to_technician_id": "12", "reason": "Nghỉ phép"},
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    async with get_sessionmaker()() as s:
        await pump(s)
    job = (await client.get(f"/api/core/jobs/{job_id}")).json()
    assert job["status"] == "completed", job
    assert sorted(sdp.puts) == ["501->12", "504->12"]
    assert job["steps"][0]["before_state"]["technicians"] == {"501": "Tech 11", "504": None}
    t12 = (await client.get("/api/workload/tickets?technician_id=12")).json()
    assert {"501", "504"} <= {t["id"] for t in t12["items"]}

    # Idempotent: a second request for a ticket already on the target does not write again.
    resp = await client.post(
        "/api/workload/tickets/reassign", json={"ticket_ids": ["501"], "to_technician_id": "12"}
    )
    async with get_sessionmaker()() as s:
        await pump(s)
    assert (await client.get(f"/api/core/jobs/{resp.json()['id']}")).json()["status"] == "completed"
    assert len(sdp.puts) == 2

    audit = (await admin.get("/api/core/audit?action=workload.reassign_requested")).json()
    assert any(e.get("message") == "Nghỉ phép" for e in audit["items"])


@respx.mock(assert_all_called=False)
async def test_manual_link_and_first_sso_login_is_viewer(admin, settings, sdp) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.core.auth.models import User
    from app.core.auth.oidc import OidcIdentity
    from app.core.auth.service import login_oidc
    from app.core.rbac.service import role_codes_for_user

    async with get_sessionmaker()() as s:
        user = await login_oidc(
            s, settings, OidcIdentity("oid-new-1", "sso.new@test.local", "SSO New", None), None
        )
        await s.commit()
        assert await role_codes_for_user(s, user.id) == ["viewer"]
        uid = await s.scalar(select(User.id).where(User.upn == "sso.new@test.local"))

    tid = "11"
    resp = await admin.put(f"/api/workload/technicians/{tid}/user", json={"user_id": str(uid)})
    assert resp.status_code == 200
    linked = {t["id"]: t for t in (await admin.get("/api/workload/technicians")).json()["items"]}[tid]
    assert linked["user"]["upn"] == "sso.new@test.local" and linked["mapped_manually"]
    assert (await admin.delete(f"/api/workload/technicians/{tid}/user")).status_code == 204
    relinked = {t["id"]: t for t in (await admin.get("/api/workload/technicians")).json()["items"]}[tid]
    assert relinked["user"]["upn"] == "wl-op1@test.local" and not relinked["mapped_manually"]


@respx.mock(assert_all_called=False)
async def test_operator_sees_only_own_tasks_supervisor_coordinates(admin, client, sdp) -> None:  # type: ignore[no-untyped-def]
    await client.login("wl-op1@test.local")
    # Asking for someone else's tasks still returns only the operator's own.
    other = (await client.get("/api/workload/tickets?technician_id=12&state=all")).json()
    assert {t["technician_id"] for t in other["items"]} == {"11"}
    unassigned = (await client.get("/api/workload/tickets?technician_id=none&state=all")).json()
    assert {t["technician_id"] for t in unassigned["items"]} <= {"11"}
    techs = (await client.get("/api/workload/technicians")).json()
    assert [t["id"] for t in techs["items"]] == ["11"] and not techs["view_all"]
    kinds = [w["kind"] for w in (await client.get("/api/core/dashboard")).json()]
    assert kinds[0] == "workload_next"  # next action first on the dashboard
    assert (
        "lifecycle_team_queue" in kinds
    )  # the team's RTR queue on the main page (operators take requests there)
    assert techs["unassigned"] == {"open": 0, "overdue": 0}
    st = (await client.get("/api/workload/stats?from=2026-11-01&to=2026-11-30&technician_id=12")).json()
    assert [t["technician_id"] for t in st["technicians"]] == ["11"]
    # The Excel report follows the same scope: only the operator's own tasks.
    from openpyxl import load_workbook

    resp = await client.get("/api/workload/tickets/export?technician_id=12&state=all")
    assert resp.status_code == 200 and "task-infra-support-" in resp.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames == ["Tổng hợp tồn", "Chi tiết task"]
    names = {r[3] for r in wb["Chi tiết task"].iter_rows(min_row=2, values_only=True)}
    assert names == {"Tech 11"}

    await client.login("wl-sup@test.local")
    techs = (await client.get("/api/workload/technicians")).json()
    assert techs["view_all"] and {"11", "12"} <= {t["id"] for t in techs["items"]}
    resp = await client.post(
        "/api/workload/tickets/reassign", json={"ticket_ids": ["504"], "to_technician_id": "11"}
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    # No system or module settings, no user management.
    assert (await client.get("/api/workload/settings")).status_code == 403
    assert (await client.get("/api/lifecycle/settings")).status_code == 403
    assert (await client.get("/api/core/users")).status_code == 403
    assert (await client.post("/api/core/connections", json={})).status_code == 403
    assert (
        await client.post(f"/api/core/jobs/{job_id}/cancel", json={"reason": "không cần nữa"})
    ).status_code == 200

    # Operators cannot see reassignment jobs (they name other operators).
    await client.login("wl-op1@test.local")
    assert (await client.get(f"/api/core/jobs/{job_id}")).status_code == 403

    await client.login("wl-mgr@test.local")
    assert (await client.get("/api/workload/settings")).status_code == 200
    assert (await client.get("/api/lifecycle/settings")).status_code == 200
    assert (await client.get("/api/core/users")).status_code == 403


def test_export_summary_counts_backlog_per_operator() -> None:
    from datetime import timedelta

    from app.modules.workload.export import days_late, detail_row, summary_rows
    from app.modules.workload.models import Ticket

    now = datetime(2026, 11, 26, 3, tzinfo=UTC)

    def t(tech: str | None, phase: str, late_days: float | None, state: str = "open") -> Ticket:
        due = now - timedelta(days=late_days) if late_days is not None else now + timedelta(days=1)
        return Ticket(
            id="1", subject="=cmd", technician_name=tech, phase=phase, state=state,
            created_at=now - timedelta(days=40), due_by=due,
            completed_at=now if state == "done" else None,
        )  # fmt: skip

    rows = summary_rows(
        [
            t("An", "pending", 3),
            t("An", "on_hold", 45),
            t("An", "in_progress", None),
            t("Bình", "pending", 0.5),
            t(None, "pending", 10),
            t("Bình", "pending", 100, state="done"),  # closed: not backlog
        ],
        now,
    )
    # Most overdue first, unassigned last, then the total.
    assert rows[0] == ["An", 3, 1, 1, 1, 2, 1, 0, 1, 40]
    assert rows[1] == ["Bình", 1, 1, 0, 0, 1, 1, 0, 0, 40]
    assert rows[2][0] == "Chưa giao" and rows[2][7] == 1
    assert rows[3] == ["Tổng cộng", 5, 3, 1, 1, 4, 2, 1, 1, 40]
    assert days_late(t("An", "pending", None), now) is None
    row = detail_row(t("An", "pending", 3), now)
    assert row[1] == "'=cmd" and row[5] == "Chưa bắt đầu" and row[10] == 3 and row[11] == 40


def test_phase_and_device_need_rules() -> None:
    s = WorkloadSettings()
    assert ticket_phase("In Progress", "open", s) == "in_progress"
    assert ticket_phase("on hold", "open", s) == "on_hold"
    assert ticket_phase("Open", "open", s) == "pending"
    assert ticket_phase("Resolved", "done", s) is None
    assert device_need("Thay thế laptop bị hỏng màn hình", s) == "replace"
    assert device_need("Xin nâng cấp RAM", s) == "upgrade"
    assert device_need("Máy in kẹt giấy – sửa chữa", s) == "repair"
    assert device_need("Cấp quyền thư mục", s) is None


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@respx.mock(assert_all_called=False)
async def test_portal_accounts_and_purchase_flow(admin, client, sdp) -> None:  # type: ignore[no-untyped-def]
    from app.modules.workload.sync import run_sync

    # A technician with no infra-ops account gets one (Operator) on the next sync.
    sdp.techs.append({"id": "14", "name": "Tech Mới", "email_id": "wl-new@test.local"})
    in_progress = req("506", "In Progress", "11", 15, ms(1, 1, 2099))
    device = req("507", "Open", "11", 16, ms(1, 1, 2099))
    device["subject"] = "Thay thế laptop hỏng bản lề"
    for r in (in_progress, device):
        r["last_updated_time"] = ms(11, 21)
        sdp.requests[r["id"]] = r
    async with get_sessionmaker()() as s:
        await run_sync(s)
    # Users are only the people an administrator adds or confirms: a sync never creates accounts.
    assert "wl-new@test.local" not in {u["upn"] for u in (await admin.get("/api/core/users")).json()}
    made = await admin.post("/api/workload/technicians/14/account")
    assert made.status_code == 201, made.text
    users = {u["upn"]: u for u in (await admin.get("/api/core/users")).json()}
    roles = {r["id"]: r["code"] for r in (await admin.get("/api/core/roles")).json()}
    assert [roles[i] for i in users["wl-new@test.local"]["role_ids"]] == ["operator"]
    again = await admin.post("/api/workload/technicians/14/account")
    assert again.json()["error"]["code"] == "already_linked"

    await client.login("wl-op1@test.local")
    work = (await client.get("/api/workload/my-work")).json()
    assert work["linked"] and work["counts"]["in_progress"] >= 1
    assert "507" in [t["id"] for t in work["suggestions"]]
    assert {t["phase"] for t in work["open"]} <= {"pending", "in_progress", "on_hold"}
    assert [
        t["id"] for t in (await client.get("/api/workload/tickets?state=in_progress")).json()["items"]
    ] == ["506"]

    # Operator proposes; the suggestion disappears; operator cannot approve.
    created = await client.post(
        "/api/workload/purchases",
        json={
            "ticket_id": "507",
            "kind": "replace",
            "item": "Laptop Dell Latitude 5440",
            "reason": "Bản lề gãy",
        },
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    assert created.json()["ticket_display_id"] == "D507" and created.json()["status"] == "proposed"
    assert "507" not in [t["id"] for t in (await client.get("/api/workload/my-work")).json()["suggestions"]]
    assert (
        await client.patch(f"/api/workload/purchases/{pid}", json={"status": "approved"})
    ).status_code == 403

    # Function Head approval evidence: magic bytes are checked.
    bad_file = await client.post(
        f"/api/workload/purchases/{pid}/evidence", files={"file": ("x.png", b"not an image", "image/png")}
    )
    assert bad_file.json()["error"]["code"] == "unsupported_file"
    up = await client.post(
        f"/api/workload/purchases/{pid}/evidence", files={"file": ("duyet.png", PNG, "image/png")}
    )
    assert up.status_code == 201 and up.json()["evidence"][0]["filename"] == "duyet.png"
    ev_id = up.json()["evidence"][0]["id"]
    assert (await client.get(f"/api/workload/purchases/{pid}/evidence/{ev_id}")).content == PNG

    await client.login("wl-mgr@test.local")
    bad = await client.patch(f"/api/workload/purchases/{pid}", json={"status": "received"})
    assert bad.json()["error"]["code"] == "invalid_transition"
    no_price = await client.patch(f"/api/workload/purchases/{pid}", json={"status": "approved"})
    assert no_price.json()["error"]["code"] == "price_required"
    cat = await client.post(
        "/api/workload/catalog",
        json={"name": "Laptop Dell Latitude 5440", "category": "Thiết bị khác", "unit_price": 25_000_000},
    )
    assert cat.status_code == 201, cat.text
    r = await client.patch(
        f"/api/workload/purchases/{pid}", json={"status": "approved", "catalog_item_id": cat.json()["id"]}
    )
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["amount"] == 25_000_000
    assert r.json()["po"]["status"] == "open"
    open_po = (await client.get("/api/workload/pos")).json()["open"][0]
    closed = await client.post(f"/api/workload/pos/{open_po['id']}/close", json=close_body(open_po))
    assert closed.status_code == 200 and closed.json()["number"].startswith("PO-")
    # Not tracked as an SDP asset (no SDP product): receiving just marks it received.
    assert (
        await client.patch(f"/api/workload/purchases/{pid}", json={"status": "received"})
    ).status_code == 400
    r = await client.post(f"/api/workload/purchases/{pid}/receive", json={})
    assert r.status_code == 200 and r.json()["status"] == "received", r.text
    listed = (await client.get("/api/workload/purchases")).json()
    assert any(p["id"] == pid for p in listed["items"])
    assert listed["counts"]["received"] == sum(p["status"] == "received" for p in listed["items"])
    x = await client.get("/api/workload/purchases/export")
    assert x.status_code == 200 and x.content[:2] == b"PK"

    # Another operator only sees their own proposals; dismissing hides a suggestion.
    await client.login("wl-op2@test.local")
    mine = (await client.get("/api/workload/purchases")).json()["items"]
    assert all(p["created_by"] == "wl-op2@test.local" for p in mine)
    # ...and cannot act on another operator's request (507 is Tech 11's).
    assert (await client.post("/api/workload/tickets/507/dismiss-purchase")).status_code == 404
    other = await client.post(
        "/api/workload/purchases", json={"ticket_id": "507", "kind": "replace", "item": "Laptop"}
    )
    assert other.status_code == 404
    await client.login("wl-op1@test.local")
    assert (await client.post("/api/workload/tickets/507/dismiss-purchase")).status_code == 204


def test_po_packing_first_fit() -> None:
    from app.modules.workload.po import pack

    assert pack([("a", 120), ("b", 90), ("c", 70), ("d", 10)], 200) == [["a", "c", "d"], ["b"]]
    # An item above the limit gets its own PO and never takes others.
    assert pack([("big", 250), ("s", 10)], 200) == [["big"], ["s"]]
    assert pack([], 200) == []


async def test_po_limit_close_and_email(client) -> None:  # type: ignore[no-untyped-def]
    """Approved proposals fill POs up to the limit, the next one opens a 2nd PO; closing freezes
    prices and the .eml draft carries the template, the workbook and the approval evidence."""
    import email
    import email.policy

    await client.login("wl-mgr@test.local")
    s = (await client.get("/api/workload/settings")).json()["values"]
    s.update(po_limit_vnd=60_000_000, po_email_to=["mua-hang@test.local"])
    assert (await client.put("/api/workload/settings", json=s)).status_code == 200
    cat = (
        await client.post(
            "/api/workload/catalog", json={"name": "Màn hình 27 inch PO", "unit_price": 20_000_000}
        )
    ).json()
    ids = []
    for qty in (2, 1, 1):  # 40M + 20M fit in PO 1; the last 20M opens PO 2
        p = (
            await client.post(
                "/api/workload/purchases",
                json={"kind": "new", "item": "Màn hình", "quantity": qty, "catalog_item_id": cat["id"],
                      "source": "onboard", "source_ref": "RTR-1", "employee": "Nguyễn Văn A"},
            )
        ).json()  # fmt: skip
        await client.post(
            f"/api/workload/purchases/{p['id']}/evidence", files={"file": ("ok.png", PNG, "image/png")}
        )
        r = await client.patch(f"/api/workload/purchases/{p['id']}", json={"status": "approved"})
        assert r.status_code == 200, r.text
        ids.append(p["id"])
    data = (await client.get("/api/workload/pos")).json()
    assert [o["total"] for o in data["open"]] == [60_000_000, 20_000_000]
    assert data["email_to"] == ["mua-hang@test.local"]
    assert (await client.get("/api/workload/purchases?source_ref=RTR-1")).json()["items"][0][
        "employee"
    ] == "Nguyễn Văn A"

    # A price change repacks the open POs (2×30M + 30M > 60M → each PO re-filled).
    upd = await client.put(f"/api/workload/catalog/{cat['id']}", json={**cat, "unit_price": 30_000_000})
    assert upd.status_code == 200
    data = (await client.get("/api/workload/pos")).json()
    assert [o["total"] for o in data["open"]] == [60_000_000, 60_000_000]
    first = data["open"][0]
    stale = await client.post(
        f"/api/workload/pos/{first['id']}/close", json={**close_body(first), "version_total": 1}
    )
    assert stale.json()["error"]["code"] == "po_changed"
    swapped = {**close_body(first), "item_ids": [i["id"] for i in data["open"][1]["items"]]}  # same total
    assert (await client.post(f"/api/workload/pos/{first['id']}/close", json=swapped)).json()["error"][
        "code"
    ] == "po_changed"

    await client.login("wl-op1@test.local")  # operators cannot close POs
    assert (
        await client.post(f"/api/workload/pos/{first['id']}/close", json=close_body(first))
    ).status_code == 403
    await client.login("wl-mgr@test.local")
    closed = (await client.post(f"/api/workload/pos/{first['id']}/close", json=close_body(first))).json()
    assert closed["status"] == "closed" and all(i["status"] == "ordered" for i in closed["items"])
    # Frozen: a later price change does not alter the closed PO.
    await client.put(f"/api/workload/catalog/{cat['id']}", json={**cat, "unit_price": 1_000_000})
    again = next(
        o for o in (await client.get("/api/workload/pos")).json()["closed"] if o["id"] == first["id"]
    )
    assert again["total"] == 60_000_000 and again["items"][0]["unit_price"] == 30_000_000

    eml = await client.get(f"/api/workload/pos/{first['id']}/email")
    assert eml.status_code == 200 and eml.headers["content-type"].startswith("message/rfc822")
    msg = email.message_from_bytes(eml.content, policy=email.policy.default)
    assert msg["X-Unsent"] == "1" and msg["To"] == "mua-hang@test.local"
    assert closed["number"] in msg["Subject"]
    names = [part.get_filename() for part in msg.iter_attachments()]
    assert names[0].endswith("-chi-tiet.xlsx") and any(n.startswith("duyet-RTR-1") for n in names[1:])
    body = msg.get_body(("plain",)).get_content()
    assert "60.000.000 đ" in body and "Màn hình 27 inch PO" in body

    # Settings reject a broken e-mail template.
    s["po_email_body"] = "Xin chào {{ po_number "
    assert (await client.put("/api/workload/settings", json=s)).json()["error"]["code"] == "invalid_settings"


@respx.mock(assert_all_called=False)
async def test_receive_creates_in_store_assets_on_sdp(client, sdp) -> None:  # type: ignore[no-untyped-def]
    """Received devices become In Store assets on SDP via a worker job; serials SDP already has are
    not created again (read first), and the purchase lists the assets."""
    await client.login("wl-mgr@test.local")
    cat = (
        await client.post(
            "/api/workload/catalog",
            json={"name": "Laptop nhận kho", "unit_price": 1_000_000, "sdp_product": "Dell Latitude 5440"},
        )
    ).json()
    assert cat["sdp_product"] == "Dell Latitude 5440"
    p = (
        await client.post(
            "/api/workload/purchases",
            json={"kind": "new", "item": "Laptop", "quantity": 2, "catalog_item_id": cat["id"]},
        )
    ).json()
    await client.post(
        f"/api/workload/purchases/{p['id']}/evidence", files={"file": ("ok.png", PNG, "image/png")}
    )
    assert (
        await client.patch(f"/api/workload/purchases/{p['id']}", json={"status": "approved"})
    ).status_code == 200
    early = await client.post(f"/api/workload/purchases/{p['id']}/receive", json={"units": []})
    assert early.json()["error"]["code"] == "not_ordered"
    po = next(
        o
        for o in (await client.get("/api/workload/pos")).json()["open"]
        if any(i["id"] == p["id"] for i in o["items"])
    )
    assert (await client.post(f"/api/workload/pos/{po['id']}/close", json=close_body(po))).status_code == 200

    queue = (await client.get("/api/core/me/next")).json()["items"]
    assert any(i["key"] == "receive:ordered" and i["href"] == "/workload/purchases/receive" for i in queue)
    target = (await client.get("/api/workload/purchases/receive-target")).json()
    assert target["connection"]["name"] == "SDP WL" and target["state"] == "In Store"
    assert target["asset_tag"]["prefix"] == "HCM" and target["asset_tag"]["codes"]["Laptop"] == "LT"

    # Operators unpack the boxes: they see the closed PO's items (no prices) and can read the handover file.
    await client.login("wl-op1@test.local")
    recv = (await client.get("/api/workload/purchases/receivable")).json()["closed"]
    item = next(i for o in recv for i in o["items"] if i["id"] == p["id"])
    assert item["unit_price"] is None and item["catalog_item"]["name"] == "Laptop nhận kho"
    assert any(i["key"] == "receive:ordered" for i in (await client.get("/api/core/me/next")).json()["items"])
    xlsx = await client.post(
        "/api/workload/purchases/receive/extract",
        files={"file": ("bb.xlsx", handover_xlsx(), "application/octet-stream")},
    )
    assert [r["serial"] for r in xlsx.json()["rows"]] == ["5CD1234XYZ", "5CD1234XZZ"], xlsx.text
    pdf = await client.post(
        "/api/workload/purchases/receive/extract",
        files={"file": ("bb.pdf", make_pdf(["BIEN BAN BAN GIAO", "Laptop Dell 5440 S/N: CN0AB123"]), "application/pdf")},
    )  # fmt: skip
    assert [r["serial"] for r in pdf.json()["rows"]] == ["CN0AB123"]
    png = await client.post(
        "/api/workload/purchases/receive/extract", files={"file": ("x.png", PNG, "image/png")}
    )
    assert png.json()["error"]["code"] == "unsupported_file"
    await client.login("wl-mgr@test.local")
    url = f"/api/workload/purchases/{p['id']}/receive"
    bad = await client.post(url, json={"units": [{"serial": "SN-1"}]})
    assert bad.json()["error"]["code"] == "units_mismatch"
    dup = await client.post(url, json={"units": [{"serial": "SN-1"}, {"serial": "sn-1"}]})
    assert dup.json()["error"]["code"] == "duplicate_serial"
    r = await client.post(
        url, json={"units": [{"serial": "SN-NEW", "asset_tag": "LT-0999"}, {"serial": "SN-OLD"}]}
    )
    assert (
        r.status_code == 200
        and r.json()["status"] == "ordered"
        and r.json()["receive_job"]["status"] == "running"
    )
    assert (await client.post(url, json={"units": [{"serial": "A1"}, {"serial": "A2"}]})).json()["error"][
        "code"
    ] == "receive_running"

    async with get_sessionmaker()() as s:
        await pump(s)
    got = next(x for x in (await client.get("/api/workload/purchases")).json()["items"] if x["id"] == p["id"])
    assert got["status"] == "received", got["receive_job"]
    assert [(a["serial"], a["id"]) for a in got["received_assets"]] == [("SN-NEW", "1000"), ("SN-OLD", "900")]
    assert sdp.asset_posts == [
        {"name": "LT-0999", "product": {"name": "Dell Latitude 5440"}, "asset_tag": "LT-0999",
         "serial_number": "SN-NEW", "state": {"name": "In Store"}}
    ]  # fmt: skip
    job = (await client.get(f"/api/core/jobs/{r.json()['receive_job']['id']}")).json()
    assert job["status"] == "completed" and job["steps"][0]["before_state"]["existing"] == {"SN-OLD": "900"}


def handover_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["BIÊN BẢN BÀN GIAO"])
    ws.append(["STT", "Tên thiết bị", "Số Serial", "Ghi chú"])
    ws.append([1, "Laptop Dell 5440", "5CD1234XYZ", ""])
    ws.append([2, "Laptop Dell 5440", "5CD1234XZZ", ""])
    ws.append([None, "Tổng", None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pdf(lines: list[str]) -> bytes:
    """Minimal one-page text PDF (ASCII), enough for pypdf text extraction."""
    text = "BT /F1 11 Tf 40 800 Td 14 TL " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_quote_rules_and_pdf_price_matching() -> None:
    from app.modules.workload.quotes import match_prices, needs_quotes, pdf_text, quote_problem

    assert needs_quotes("laptop ", ["PC", "Laptop"]) and not needs_quotes("Màn hình", ["PC", "Laptop"])
    assert quote_problem([], 2, 20_000_000, 3)  # a real price is always needed
    assert quote_problem([25_000_000, 21_000_000], 0, 20_000_000, 3)  # above threshold: 3 needed
    assert quote_problem([25_000_000, 21_000_000, 22_000_000], 0, 20_000_000, 3) is None
    assert quote_problem([25_000_000, 21_000_000], 1, 20_000_000, 3) is None  # a vendor out of stock counts
    assert quote_problem([19_500_000], 0, 20_000_000, 3) is None  # one quote enough under the threshold
    text = pdf_text(make_pdf(["STT Ten hang SL Don gia Thanh tien", "1 Chuot Logitech M90 10 145,000 1,450,000",
                              "2 Man hinh Dell P2422H 2 4.150.000 8.300.000", "Tong cong 9.750.000"]))  # fmt: skip
    found = match_prices(
        text,
        [{"id": "a", "name": "Chuột Logitech M90"}, {"id": "b", "name": "Màn hình Dell P2422H"},
         {"id": "c", "name": "Laptop Dell XPS 13"}],
    )  # fmt: skip
    assert {f["id"]: f["price"] for f in found} == {"a": 145_000, "b": 4_150_000}


async def test_operator_sees_no_prices_and_3_quote_process(client) -> None:  # type: ignore[no-untyped-def]
    await client.login("wl-mgr@test.local")
    laptop = (
        await client.post("/api/workload/catalog", json={"name": "Laptop báo giá", "category": "Laptop"})
    ).json()
    mouse = (
        await client.post(
            "/api/workload/catalog", json={"name": "Chuột Logitech M90", "category": "Phụ kiện"}
        )
    ).json()

    # Operator: requests a device only and never sees prices, quotes or POs.
    await client.login("wl-op1@test.local")
    cat = (await client.get("/api/workload/catalog")).json()
    assert cat and all("unit_price" not in c and "vendor" not in c for c in cat)
    p = (
        await client.post(
            "/api/workload/purchases",
            json={"kind": "new", "item": "Laptop", "quantity": 2, "catalog_item_id": laptop["id"]},
        )
    ).json()
    assert p["unit_price"] is None and p["quotes"] == [] and p["quote_rule"] is None
    await client.post(
        f"/api/workload/purchases/{p['id']}/evidence", files={"file": ("ok.png", PNG, "image/png")}
    )
    assert (await client.get("/api/workload/pos")).status_code == 403
    assert (await client.post(f"/api/workload/purchases/{p['id']}/quotes", data={"vendor": "X", "unit_price": "1"},
                              files={"file": ("q.pdf", make_pdf(["x"]), "application/pdf")})).status_code == 403  # fmt: skip

    # Supervisor: 3-quote process, lowest quote wins.
    await client.login("wl-mgr@test.local")
    url = f"/api/workload/purchases/{p['id']}"
    not_pdf = await client.post(f"{url}/quotes", data={"vendor": "FPT", "unit_price": "25000000"},
                                files={"file": ("q.pdf", PNG, "application/pdf")})  # fmt: skip
    assert not_pdf.json()["error"]["code"] == "unsupported_file"
    for vendor, price in (("FPT", 25_000_000), ("Phong Vũ", 24_000_000)):
        r = await client.post(f"{url}/quotes", data={"vendor": vendor, "unit_price": str(price)},
                              files={"file": (f"{vendor}.pdf", make_pdf([vendor]), "application/pdf")})  # fmt: skip
        assert r.status_code == 201, r.text
    assert r.json()["quote_rule"]["problem"] and r.json()["unit_price"] == 24_000_000
    blocked = await client.patch(url, json={"status": "approved"})
    assert blocked.json()["error"]["code"] == "quotes_required"
    r = await client.post(f"{url}/quotes", data={"vendor": "An Phát", "unit_price": "23500000"},
                          files={"file": ("ap.pdf", make_pdf(["AP"]), "application/pdf")})  # fmt: skip
    assert r.json()["quote_rule"]["problem"] is None
    assert [q["lowest"] for q in r.json()["quotes"]] == [True, False, False]
    ok = await client.patch(url, json={"status": "approved"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["unit_price"] == 23_500_000 and ok.json()["amount"] == 47_000_000
    qid = ok.json()["quotes"][0]["id"]
    assert (await client.get(f"{url}/quotes/{qid}")).content.startswith(b"%PDF")
    assert (await client.delete(f"{url}/quotes/{qid}")).json()["error"]["code"] == "quotes_locked"

    # Other items: price list updated from the vendor's PDF quote.
    up = await client.post(
        "/api/workload/catalog/quote-pdf",
        files={
            "file": ("bg.pdf", make_pdf(["1 Chuot Logitech M90 10 145.000 1.450.000"]), "application/pdf")
        },
    )
    assert up.status_code == 200, up.text
    sug = [x for x in up.json()["suggestions"] if x["id"] == mouse["id"]]
    assert sug and sug[0]["price"] == 145_000
    applied = await client.post(
        "/api/workload/catalog/apply-quote",
        json={"file_id": up.json()["file_id"], "vendor": "Phong Vũ", "quoted_at": "2026-11-24",
              "prices": [{"id": mouse["id"], "unit_price": sug[0]["price"]}]},
    )  # fmt: skip
    assert applied.status_code == 200 and applied.json()[0]["unit_price"] == 145_000
    assert (await client.get(f"/api/workload/catalog/{mouse['id']}/quote-file")).content.startswith(b"%PDF")
    await client.login("wl-op1@test.local")
    assert (await client.get(f"/api/workload/catalog/{mouse['id']}/quote-file")).status_code == 403
    mine = next(
        x for x in (await client.get("/api/workload/purchases")).json()["items"] if x["id"] == p["id"]
    )
    assert mine["status"] == "approved" and mine["amount"] is None and mine["po"] is None


def test_supplier_detection_and_price_position() -> None:
    from app.modules.workload.quotes import detect_supplier, match_prices, pick_price

    sups = [
        {"name": "FPT", "keywords": ["0101248141", "FPT Shop"]},
        {"name": "An Phát", "keywords": ["anphatpc.com.vn"]},
    ]
    assert detect_supplier("CÔNG TY ... MST: 0101248141", sups)["name"] == "FPT"
    assert detect_supplier("www.AnPhatPC.com.vn", sups)["name"] == "An Phát"
    assert detect_supplier("Công ty khác", sups) is None
    assert (
        pick_price([2, 25_900_000, 51_800_000], 0) == 2
        and pick_price([2, 25_900_000, 51_800_000], 2) == 25_900_000
    )
    assert pick_price([1], 3) is None
    # A vendor form "qty | list price | discounted unit price | total": the 3rd amount is the price.
    line = "1 Laptop Dell Latitude 5440 2 27.000.000 25.900.000 51.800.000"
    assert (
        match_prices(line, [{"id": "x", "name": "Laptop Dell Latitude 5440"}], position=2)[0]["price"]
        == 25_900_000
    )
    assert match_prices(line, [{"id": "x", "name": "Laptop Dell Latitude 5440"}])[0]["price"] == 25_900_000


async def test_supplier_recognition_out_of_stock_and_approval_email(client) -> None:  # type: ignore[no-untyped-def]
    import email
    import email.policy

    await client.login("wl-mgr@test.local")
    sup = await client.post(
        "/api/workload/suppliers",
        json={
            "name": "Hoàng Hà Test",
            "keywords": ["0309999999"],
            "price_position": 2,
            "website": "https://hh.example",
        },
    )
    assert sup.status_code == 201, sup.text
    assert (await client.post("/api/workload/suppliers", json={"name": "No kw", "keywords": []})).json()[
        "error"
    ]["code"] == "keywords_required"
    laptop = (
        await client.post("/api/workload/catalog", json={"name": "Laptop ThinkPad E14", "category": "Laptop"})
    ).json()
    p = (
        await client.post(
            "/api/workload/purchases",
            json={
                "kind": "new",
                "item": "Laptop",
                "quantity": 1,
                "catalog_item_id": laptop["id"],
                "employee": "Nguyễn A",
            },
        )
    ).json()
    await client.post(
        f"/api/workload/purchases/{p['id']}/evidence", files={"file": ("ok.png", PNG, "image/png")}
    )
    url = f"/api/workload/purchases/{p['id']}/quotes"

    # Read a quote PDF: the vendor is recognised by its tax code, the price by its form's column.
    pdf = make_pdf(
        ["CONG TY HOANG HA - MST 0309999999", "1 Laptop ThinkPad E14 1 24.000.000 22.500.000 22.500.000"]
    )
    read = (await client.post(f"{url}/read", files={"file": ("hh.pdf", pdf, "application/pdf")})).json()
    assert read["supplier"]["name"] == "Hoàng Hà Test" and read["unit_price"] == 22_500_000
    r = await client.post(url, data={"vendor": "Hoàng Hà Test", "unit_price": str(read["unit_price"]),
                                     "supplier_id": read["supplier"]["id"], "file_id": read["file_id"]})  # fmt: skip
    assert r.status_code == 201, r.text
    r = await client.post(url, data={"vendor": "FPT", "unit_price": "23000000"},
                          files={"file": ("fpt.pdf", make_pdf(["FPT"]), "application/pdf")})  # fmt: skip
    assert r.json()["quote_rule"]["problem"]  # 2 of 3

    # The third vendor has no stock: screenshot of its web store instead of a price.
    no_file = await client.post(url, data={"vendor": "An Phát", "out_of_stock": "true"})
    assert no_file.json()["error"]["code"] == "file_required"
    r = await client.post(url, data={"vendor": "An Phát", "out_of_stock": "true"},
                          files={"file": ("store.png", PNG, "image/png")})  # fmt: skip
    assert r.status_code == 201, r.text
    oos = [q for q in r.json()["quotes"] if q["out_of_stock"]]
    assert oos and oos[0]["unit_price"] is None and oos[0]["mime"] == "image/png"
    assert r.json()["quote_rule"]["problem"] is None and r.json()["unit_price"] == 22_500_000
    assert (await client.get(f"{url}/{oos[0]['id']}")).headers["content-type"] == "image/png"
    assert (
        await client.patch(f"/api/workload/purchases/{p['id']}", json={"status": "approved"})
    ).status_code == 200

    # The approval e-mail (Head of IT / Finance) shows the comparison and attaches every proof.
    po = next(
        o
        for o in (await client.get("/api/workload/pos")).json()["open"]
        if any(i["id"] == p["id"] for i in o["items"])
    )
    closed = await client.post(f"/api/workload/pos/{po['id']}/close", json=close_body(po))
    assert closed.status_code == 200, closed.text
    msg = email.message_from_bytes(
        (await client.get(f"/api/workload/pos/{po['id']}/email")).content, policy=email.policy.default
    )
    html = msg.get_body(("html",)).get_content()
    assert (
        "<table" in html
        and "Hoàng Hà Test 22.500.000 đ (chọn – thấp nhất)" in html
        and "An Phát: hết hàng" in html
    )
    assert "Trình phê duyệt" in msg["Subject"] or "Head of IT" in msg.get_body(("plain",)).get_content()
    names = [a.get_filename() for a in msg.iter_attachments()]
    assert any(n.startswith("het-hang-") and n.endswith("store.png") for n in names)
    assert sum(n.startswith("bao-gia-") for n in names) == 2


async def test_bulk_approve_applies_eligible_and_reports_the_rest(client) -> None:  # type: ignore[no-untyped-def]
    await client.login("wl-mgr@test.local")
    cat = (
        await client.post("/api/workload/catalog", json={"name": "Tai nghe bulk", "unit_price": 500_000})
    ).json()
    ids = []
    for evidence in (True, True, False):
        p = (
            await client.post(
                "/api/workload/purchases",
                json={"kind": "new", "item": "Tai nghe", "quantity": 1, "catalog_item_id": cat["id"]},
            )
        ).json()
        if evidence:
            await client.post(
                f"/api/workload/purchases/{p['id']}/evidence", files={"file": ("ok.png", PNG, "image/png")}
            )
        ids.append(p["id"])
    assert p["created_by_name"]  # requester shown by name, not only the e-mail
    r = await client.post("/api/workload/purchases/bulk", json={"ids": ids, "status": "approved"})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["done"]) == sorted(ids[:2]) and list(r.json()["failed"]) == [ids[2]]
    items = {x["id"]: x for x in (await client.get("/api/workload/purchases")).json()["items"]}
    assert [items[i]["status"] for i in ids] == ["approved", "approved", "proposed"]
    assert items[ids[0]]["po"] is not None  # packed into an open PO
    rej = await client.post("/api/workload/purchases/bulk", json={"ids": [ids[2]], "status": "rejected"})
    assert "lý do" in rej.json()["failed"][ids[2]]
    await client.login("wl-op1@test.local")
    assert (
        await client.post("/api/workload/purchases/bulk", json={"ids": ids, "status": "approved"})
    ).status_code == 403


@respx.mock(assert_all_called=False)
async def test_next_actions_queue(client, sdp) -> None:  # type: ignore[no-untyped-def]
    from app.modules.workload.sync import run_sync

    async with get_sessionmaker()() as s:
        await run_sync(s)
    await client.login("wl-op1@test.local")
    p = (
        await client.post(
            "/api/workload/purchases", json={"kind": "new", "item": "Chuột queue", "quantity": 1}
        )
    ).json()
    q = (await client.get("/api/core/me/next")).json()
    assert q["failed"] == []
    keys = [i["key"] for i in q["items"]]
    assert f"evidence:{p['id']}" in keys
    overdue = [i for i in q["items"] if i["kind"] == "ticket" and i["priority"] == 1]
    assert len(overdue) == 1 and "quá hạn" in overdue[0]["title"]  # one reminder, not one row per task
    assert overdue[0]["href"] == "/workload/tickets?tech=me&state=overdue"  # lands on *my* overdue tasks
    summary = (await client.get("/api/core/me/summary")).json()
    assert summary["failed"] == []
    groups = {i["key"]: i for i in summary["items"]}
    assert {"tickets:done", "tickets:late", "tickets:soon", "tickets:open"} <= set(groups)
    assert (
        groups["tickets:late"]["count"] >= 1
        and groups["tickets:open"]["count"] >= groups["tickets:late"]["count"]
    )
    assert [i["priority"] for i in q["items"]] == sorted(i["priority"] for i in q["items"])
    assert not any(i["kind"] == "purchase_approval" for i in q["items"])  # operators do not approve

    await client.post(
        f"/api/workload/purchases/{p['id']}/evidence", files={"file": ("ok.png", PNG, "image/png")}
    )
    await client.login("wl-mgr@test.local")
    q = (await client.get("/api/core/me/next")).json()
    approval = [i for i in q["items"] if i["kind"] == "purchase_approval"]
    assert approval and approval[0]["href"] == "/workload/purchases?status=proposed"


@respx.mock(assert_all_called=False)
async def test_unlinked_operator_is_told_first(admin, client, sdp) -> None:  # type: ignore[no-untyped-def]
    # wl-op9 has no SDP technician with that e-mail: its tasks would be silently missing.
    await _user(admin, "operator", "wl-op9@test.local")
    await client.login("wl-op9@test.local")
    for c in (
        await client.get("/api/core/me/credentials")
    ).json():  # other tests' fixtures may have given one
        await client.delete(f"/api/core/me/credentials/{c['profile_id']}")
    q = (await client.get("/api/core/me/next")).json()
    top = {i["key"]: i for i in q["items"] if i["priority"] == 0}
    assert top["tickets:unlinked"]["passive"] is True
    # A new operator has no personal SDP key yet: asked once per SDP connection in use (sources dedupe by key).
    keys = [i for i in q["items"] if i["kind"] == "personal_key"]
    assert keys and len({k["key"] for k in keys}) == len(keys)
    assert any("SDP WL" in k["title"] for k in keys) and all(k["href"] == "/me/credentials" for k in keys)
    await client.login("wl-mgr@test.local")  # sees all tasks: no notice
    assert all(
        i["key"] != "tickets:unlinked" for i in (await client.get("/api/core/me/next")).json()["items"]
    )


async def test_close_refuses_items_without_price(client) -> None:  # type: ignore[no-untyped-def]
    """The price list was cleared after approval: closing would freeze no price and undercount the PO."""
    await client.login("wl-mgr@test.local")
    cat = (
        await client.post("/api/workload/catalog", json={"name": "Bàn phím no-price", "unit_price": 300_000})
    ).json()
    p = (
        await client.post(
            "/api/workload/purchases",
            json={"kind": "new", "item": "Bàn phím", "quantity": 1, "catalog_item_id": cat["id"]},
        )
    ).json()
    await client.post(
        f"/api/workload/purchases/{p['id']}/evidence", files={"file": ("ok.png", PNG, "image/png")}
    )
    assert (
        await client.patch(f"/api/workload/purchases/{p['id']}", json={"status": "approved"})
    ).status_code == 200
    assert (
        await client.put(f"/api/workload/catalog/{cat['id']}", json={**cat, "unit_price": None})
    ).status_code == 200
    po = next(
        o
        for o in (await client.get("/api/workload/pos")).json()["open"]
        if any(i["id"] == p["id"] for i in o["items"])
    )
    r = await client.post(f"/api/workload/pos/{po['id']}/close", json=close_body(po))
    assert r.json()["error"]["code"] == "price_required"
    # Price restored: the PO can be closed again.
    await client.put(f"/api/workload/catalog/{cat['id']}", json={**cat, "unit_price": 300_000})
    po = next(o for o in (await client.get("/api/workload/pos")).json()["open"] if o["id"] == po["id"])
    assert (await client.post(f"/api/workload/pos/{po['id']}/close", json=close_body(po))).status_code == 200


@respx.mock(assert_all_called=False)
async def test_operator_retries_failed_receive_and_serials_are_unique(client, sdp) -> None:  # type: ignore[no-untyped-def]
    await client.login("wl-mgr@test.local")
    cat = (
        await client.post(
            "/api/workload/catalog",
            json={"name": "Laptop retry", "unit_price": 1_000_000, "sdp_product": "Dell Latitude 5440"},
        )
    ).json()
    ids = []
    for _ in range(2):
        p = (
            await client.post(
                "/api/workload/purchases",
                json={"kind": "new", "item": "Laptop", "quantity": 1, "catalog_item_id": cat["id"]},
            )
        ).json()
        await client.post(
            f"/api/workload/purchases/{p['id']}/evidence", files={"file": ("ok.png", PNG, "image/png")}
        )
        assert (
            await client.patch(f"/api/workload/purchases/{p['id']}", json={"status": "approved"})
        ).status_code == 200
        ids.append(p["id"])
    for po in (await client.get("/api/workload/pos")).json()["open"]:
        if any(i["id"] in ids for i in po["items"]):
            assert (
                await client.post(f"/api/workload/pos/{po['id']}/close", json=close_body(po))
            ).status_code == 200

    # The operator receives; SDP fails; they can follow and retry the job.
    await client.login("wl-op1@test.local")
    sdp.fail_assets = True
    r = await client.post(f"/api/workload/purchases/{ids[0]}/receive", json={"units": [{"serial": "RT-001"}]})
    assert r.status_code == 200, r.text
    job_id = r.json()["receive_job"]["id"]
    async with get_sessionmaker()() as s:
        await pump(s)
    job = await client.get(f"/api/core/jobs/{job_id}")
    assert job.status_code == 200 and job.json()["status"] == "failed", job.text

    # The same serial (any case) cannot be received for another purchase, even while the job is failed.
    dup = await client.post(
        f"/api/workload/purchases/{ids[1]}/receive", json={"units": [{"serial": "rt-001"}]}
    )
    assert dup.json()["error"]["code"] == "serial_used" and "rt-001" in dup.json()["error"]["message"].lower()

    sdp.fail_assets = False
    assert (await client.post(f"/api/core/jobs/{job_id}/steps/receive/retry")).status_code == 200
    async with get_sessionmaker()() as s:
        await pump(s)
    assert (await client.get(f"/api/core/jobs/{job_id}")).json()["status"] == "completed"
    # Received now: still taken for other purchases.
    dup = await client.post(
        f"/api/workload/purchases/{ids[1]}/receive", json={"units": [{"serial": "RT-001"}]}
    )
    assert dup.json()["error"]["code"] == "serial_used"
    ok = await client.post(
        f"/api/workload/purchases/{ids[1]}/receive", json={"units": [{"serial": "RT-002"}]}
    )
    assert ok.status_code == 200, ok.text


async def test_unused_quote_files_are_cleaned_up(client) -> None:  # type: ignore[no-untyped-def]
    """Quote PDFs read by "Đọc báo giá" are audited; after a week, unattached ones are deleted."""
    import uuid
    from datetime import timedelta

    from sqlalchemy import select, update

    from app.core.audit.models import AuditLog
    from app.core.storage.models import StoredFile
    from app.core.storage.service import get_storage
    from app.modules.workload.purchases_api import QUOTE_FILE_READ, cleanup_quote_files

    await client.login("wl-mgr@test.local")
    cat = (await client.post("/api/workload/catalog", json={"name": "Chuot cleanup"})).json()
    pdf = make_pdf(["1 Chuot cleanup 1 99.000 99.000"])
    read = []
    for _ in range(2):
        r = await client.post(
            "/api/workload/catalog/quote-pdf", files={"file": ("bg.pdf", pdf, "application/pdf")}
        )
        assert r.status_code == 200, r.text
        read.append(r.json()["file_id"])
    used, unused = read
    applied = await client.post(
        "/api/workload/catalog/apply-quote",
        json={"file_id": used, "vendor": "Test", "prices": [{"id": cat["id"], "unit_price": 99_000}]},
    )
    assert applied.status_code == 200, applied.text
    # An old evidence upload (not read as a quote) is never touched.
    p = (
        await client.post("/api/workload/purchases", json={"kind": "new", "item": "Chuột", "quantity": 1})
    ).json()
    await client.post(
        f"/api/workload/purchases/{p['id']}/evidence", files={"file": ("ok.png", PNG, "image/png")}
    )

    async with get_sessionmaker()() as s:
        audited = select(AuditLog.id).where(AuditLog.action == QUOTE_FILE_READ, AuditLog.target_id == unused)
        assert await s.scalar(audited)
        key = (await s.get(StoredFile, uuid.UUID(unused))).storage_key  # type: ignore[union-attr]
        assert (await cleanup_quote_files(s))["deleted"] == 0  # too recent
        old = datetime.now(UTC) - timedelta(days=8)
        mine = StoredFile.uploaded_by == "wl-mgr@test.local"  # the quote PDFs and the evidence above
        await s.execute(update(StoredFile).where(mine).values(created_at=old))
        await s.commit()
        assert (await cleanup_quote_files(s))["deleted"] == 1
        assert await s.get(StoredFile, uuid.UUID(unused)) is None
        assert await s.get(StoredFile, uuid.UUID(used)) is not None  # referenced by the price list
        assert not get_storage()._path(key).exists()
    assert (await client.get(f"/api/workload/catalog/{cat['id']}/quote-file")).content.startswith(b"%PDF")
    ev = (await client.get("/api/workload/purchases")).json()["items"]
    ev_id = next(x for x in ev if x["id"] == p["id"])["evidence"][0]["id"]
    assert (await client.get(f"/api/workload/purchases/{p['id']}/evidence/{ev_id}")).content == PNG


async def test_sync_closes_stored_tickets_with_vietnamese_done_status(sdp) -> None:  # type: ignore[no-untyped-def]
    """A ticket stored as open (rule did not know "Đã xử lý") is closed by the next sync's reclassify."""
    from sqlalchemy import select

    from app.modules.workload.models import Ticket
    from app.modules.workload.settings import load
    from app.modules.workload.sync import reclassify

    async with get_sessionmaker()() as s:
        t = (await s.scalars(select(Ticket).where(Ticket.state == "open").limit(1))).one()
        t.status, t.phase = "Đã xử lý", "pending"
        await s.flush()
        await reclassify(s, await load(s))
        assert (t.state, t.phase) == ("done", None)
        await s.rollback()


async def test_tasks_of_operators_not_in_app_are_hidden(admin, sdp) -> None:  # type: ignore[no-untyped-def]
    """A technician nobody added to infra-ops stays synced but is out of the task list, cards and statistics."""
    from app.modules.workload.models import Technician, Ticket

    async with get_sessionmaker()() as s:
        s.add(Technician(id="9901", name="Ngoài App", email="ngoai.app@test.local", active=True))
        s.add(
            Ticket(
                id="99001",
                display_id="99001",
                subject="Task ngoài app",
                technician_id="9901",
                technician_name="Ngoài App",
                status="Open",
                state="open",
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()
    listed = {
        t["id"] for t in (await admin.get("/api/workload/tickets", params={"state": "all"})).json()["items"]
    }
    assert "99001" not in listed
    cards = {t["id"] for t in (await admin.get("/api/workload/technicians")).json()["items"]}
    assert "9901" not in cards
    # Settings still list it, so an administrator can add the account.
    every = {
        t["id"]: t
        for t in (await admin.get("/api/workload/technicians", params={"all": "1"})).json()["items"]
    }
    assert every["9901"]["user"] is None
    assert (await admin.post("/api/workload/technicians/9901/account")).status_code == 201
    listed = {
        t["id"] for t in (await admin.get("/api/workload/tickets", params={"state": "all"})).json()["items"]
    }
    assert "99001" in listed
