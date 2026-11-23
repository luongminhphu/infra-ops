"""Workload module: SDP ticket sync, operator scoping, reassignment write-back, statistics."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.core.db import get_sessionmaker
from app.core.workflow.engine import pump
from app.modules.workload.rules import device_need, is_overdue, normalize_ticket, pick_bucket, ticket_phase
from app.modules.workload.settings import WorkloadSettings

SDP = "https://sdp-wl.test.local"
OK = {"response_status": {"status_code": 2000, "status": "success"}}


def ms(month: int, day: int, year: int = 2026) -> dict[str, str]:
    return {"value": str(int(datetime(year, month, day, 3, tzinfo=UTC).timestamp() * 1000))}


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
def test_normalize_and_overdue_rules() -> None:
    s = WorkloadSettings()
    t = normalize_ticket(req("1", "resolved", "7", 1, ms(11, 5), 4), s)
    assert t["state"] == "done" and t["technician_id"] == "7" and t["completed_at"] is not None
    assert not is_overdue(t, datetime(2026, 12, 1, tzinfo=UTC))
    late = normalize_ticket(req("2", "Closed", "7", 1, ms(11, 2), 6), s)
    assert is_overdue(late, datetime(2026, 12, 1, tzinfo=UTC))
    # Re-opened tickets drop a stale completion time.
    reopened = normalize_ticket(req("3", "Open", None, 1, ms(11, 2), 6), s)
    assert reopened["state"] == "open" and reopened["completed_at"] is None
    assert is_overdue(reopened, datetime(2026, 11, 3, tzinfo=UTC))
    assert not is_overdue(reopened, datetime(2026, 11, 1, tzinfo=UTC))
    assert normalize_ticket(req("4", "Canceled", None, 1, None), s)["state"] == "cancelled"
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
        self.techs = [
            {"id": "11", "name": "Tech 11", "email_id": "wl-op1@test.local"},
            {"id": "12", "name": "Tech 12", "email_id": "wl-op2@test.local"},
        ]

    def install(self, r: respx.MockRouter) -> None:
        r.get(f"{SDP}/api/v3/technicians").mock(
            side_effect=lambda _: httpx.Response(
                200, json={**OK, "list_info": {"has_more_rows": False}, "technicians": self.techs}
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
    return fake


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
    assert techs["unassigned"] == {"open": 0, "overdue": 0}
    st = (await client.get("/api/workload/stats?from=2026-11-01&to=2026-11-30&technician_id=12")).json()
    assert [t["technician_id"] for t in st["technicians"]] == ["11"]

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
    users = {u["upn"]: u for u in (await admin.get("/api/core/users")).json()}
    assert "wl-new@test.local" in users
    roles = {r["id"]: r["code"] for r in (await admin.get("/api/core/roles")).json()}
    assert [roles[i] for i in users["wl-new@test.local"]["role_ids"]] == ["operator"]

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

    await client.login("wl-mgr@test.local")
    bad = await client.patch(f"/api/workload/purchases/{pid}", json={"status": "received"})
    assert bad.json()["error"]["code"] == "invalid_transition"
    for st in ("approved", "ordered", "received"):
        r = await client.patch(f"/api/workload/purchases/{pid}", json={"status": st})
        assert r.status_code == 200 and r.json()["status"] == st
    listed = (await client.get("/api/workload/purchases")).json()
    assert any(p["id"] == pid for p in listed["items"])
    assert listed["counts"]["received"] == sum(p["status"] == "received" for p in listed["items"])
    x = await client.get("/api/workload/purchases/export")
    assert x.status_code == 200 and x.content[:2] == b"PK"

    # Another operator only sees their own proposals; dismissing hides a suggestion.
    await client.login("wl-op2@test.local")
    mine = (await client.get("/api/workload/purchases")).json()["items"]
    assert all(p["created_by"] == "wl-op2@test.local" for p in mine)
    assert (await client.post("/api/workload/tickets/507/dismiss-purchase")).status_code == 204
