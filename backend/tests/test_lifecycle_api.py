"""End-to-end Phase 2: settings → sync (mocked SDP/BioStar) → reconciliation API → export."""

from __future__ import annotations

import io
import json
import re
from datetime import date

import httpx
import pytest
import respx
from openpyxl import load_workbook

from app.core.db import get_sessionmaker
from tests.lifecycle_fixtures import (
    ASSETS,
    BIOSTAR_USERS,
    DETAILS,
    HIDDEN_SDP_USERS,
    REQUESTS,
    SDP_USERS,
    sdp_ok,
)

SDP = "https://sdp-e2e.test.local:8080"
BS = "https://bs-e2e.test.local"


def mock_systems(router: respx.MockRouter) -> dict[str, respx.Route]:
    routes = {}
    routes["list"] = router.get(f"{SDP}/api/v3/requests").mock(
        return_value=httpx.Response(200, json=sdp_ok("requests", REQUESTS))
    )

    def detail(request: httpx.Request, rid: str) -> httpx.Response:
        return httpx.Response(200, json={"request": {"id": rid, "description": DETAILS[rid]}})

    routes["detail"] = router.get(url__regex=rf"{re.escape(SDP)}/api/v3/requests/(?P<rid>\d+)$").mock(
        side_effect=detail
    )

    # Users come back in two pages to exercise pagination; a search by employee ID also finds users
    # the list skipped (HIDDEN_SDP_USERS).
    def users(request: httpx.Request) -> httpx.Response:
        info = json.loads(request.url.params.get("input_data", "{}")).get("list_info", {})
        if crit := info.get("search_criteria"):
            hits = [u for u in SDP_USERS + HIDDEN_SDP_USERS if crit["value"] in (u.get("employee_id") or "")]
            return httpx.Response(200, json=sdp_ok("users", hits))
        first = info.get("start_index", 1) == 1
        page = SDP_USERS[:2] if first else SDP_USERS[2:]
        return httpx.Response(200, json=sdp_ok("users", page, has_more=first))

    routes["users"] = router.get(f"{SDP}/api/v3/users").mock(side_effect=users)
    routes["assets"] = router.get(f"{SDP}/api/v3/assets").mock(
        return_value=httpx.Response(200, json=sdp_ok("assets", ASSETS))
    )
    router.post(f"{BS}/api/login").mock(
        return_value=httpx.Response(200, headers={"bs-session-id": "s"}, json={"User": {}})
    )
    routes["bs_users"] = router.get(f"{BS}/api/users").mock(
        return_value=httpx.Response(
            200, json={"UserCollection": {"total": str(len(BIOSTAR_USERS)), "rows": BIOSTAR_USERS}}
        )
    )
    router.post(f"{BS}/api/logout").mock(return_value=httpx.Response(200, json={}))
    return routes


@pytest.fixture
async def configured(admin):  # type: ignore[no-untyped-def]
    existing = {p["name"]: p for p in (await admin.get("/api/core/connections")).json()}
    sdp = (
        existing.get("SDP E2E")
        or (
            await admin.post(
                "/api/core/connections",
                json={
                    "system_type": "sdp",
                    "name": "SDP E2E",
                    "base_url": SDP,
                    "config": {"verify_tls": False},
                },
            )
        ).json()
    )
    await admin.put(
        f"/api/core/connections/{sdp['id']}/credentials/read",
        json={"auth_type": "api_key", "secrets": {"api_key": "k-e2e-0000000001"}},
    )
    bs = (
        existing.get("BS E2E")
        or (
            await admin.post(
                "/api/core/connections",
                json={
                    "system_type": "biostar",
                    "name": "BS E2E",
                    "base_url": BS,
                    "config": {"verify_tls": False},
                },
            )
        ).json()
    )
    await admin.put(
        f"/api/core/connections/{bs['id']}/credentials/read",
        json={"auth_type": "username_password", "public": {"username": "r"}, "secrets": {"password": "p"}},
    )
    settings = (await admin.get("/api/lifecycle/settings")).json()
    values = {
        **settings["values"],
        "sdp_profile_id": sdp["id"],
        "biostar_profile_id": bs["id"],
        "change_type_map": {"Chính thức": "probation_to_official"},
    }
    resp = await admin.put("/api/lifecycle/settings", json=values)
    assert resp.status_code == 200, resp.text
    return sdp, bs


async def _sync() -> dict:  # type: ignore[type-arg]
    from app.modules.lifecycle.sync import run_sync

    async with get_sessionmaker()() as s:
        return await run_sync(s, trigger="manual", requested_by="admin@test.local", today=date(2026, 11, 16))


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_end_to_end_sync_and_reconciliation(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]
    routes = mock_systems(respx_mock)
    values = (await admin.get("/api/lifecycle/settings")).json()["values"]
    r = await admin.put("/api/lifecycle/settings", json={**values, "biostar_excluded_groups": ["3. vendor"]})
    assert r.status_code == 200, r.text
    summary = await _sync()
    assert summary["status"] == "success", summary
    # SDP 003261 is missing from the paged list but found by searching the employee ID of BioStar 103261;
    # the vendor group is not reconciled at all.
    assert summary["requests_fetched"] == 3 and summary["sdp_users"] == 4 and summary["biostar_users"] == 4
    assert summary["assets"] == 3  # printer filtered out by product types
    assert summary["jobs_created"] == 2  # onboard 003551 + offboard 002100 (1003 has parse errors)
    jobs = (await admin.get("/api/core/jobs", params={"status": "draft"})).json()["items"]
    assert {(j["workflow_key"], j["subject_key"]) for j in jobs} >= {
        ("lifecycle.onboard", "003551"),
        ("lifecycle.offboard", "002100"),
    }
    assert routes["list"].calls[0].request.headers["authtoken"] == "k-e2e-0000000001"
    criteria = json.loads(routes["list"].calls[0].request.url.params["input_data"])["list_info"][
        "search_criteria"
    ]
    assert criteria[0] == {"field": "requester.name", "condition": "is", "value": "RTR"}

    data = (await admin.get("/api/lifecycle/reconciliation")).json()
    by_emp = {r["employee_id"]: r for r in data["items"]}
    assert set(by_emp) == {"003551", "002100", "001500", "009999", "003261"}
    # Office 103261 = SDP 003261: matched, so no "BioStar user without SDP user" issue.
    assert not by_emp["003261"]["issues"], by_emp["003261"]
    assert HIDDEN_SDP_USERS[0]["employee_id"] == "003261"
    assert data["items"][0]["max_severity"] == "high"  # sorted by severity
    assert {i["rule"] for i in by_emp["002100"]["issues"]} >= {
        "offboard_biostar_active",
        "offboard_assets_in_use",
    }
    assert data["run"]["status"] == "success"
    assert any(n["rule"] == "request_parse_error" for n in data["notes"])

    # Filters, diacritic-insensitive search
    only = (await admin.get("/api/lifecycle/reconciliation", params={"q": "nguyen van an"})).json()
    assert [r["employee_id"] for r in only["items"]] == ["003551"]
    high = (await admin.get("/api/lifecycle/reconciliation", params={"severity": "high"})).json()
    assert [r["employee_id"] for r in high["items"]] == ["002100"]

    # Detail with raw records
    detail = (await admin.get("/api/lifecycle/reconciliation/002100")).json()
    assert detail["assets"][0]["asset_tag"] == "LT0123"
    assert detail["biostar"]["access_groups"] == ["Văn phòng HCM"]
    assert detail["requests"][0]["kind"] == "offboard"

    # Acknowledge → removed from counts, reopen restores
    ack = await admin.post(
        "/api/lifecycle/reconciliation/002100/ack",
        json={"rule": "offboard_biostar_active", "note": "Đã báo bảo vệ, chờ xoá"},
    )
    assert ack.status_code == 204
    row = (await admin.get("/api/lifecycle/reconciliation/002100")).json()["row"]
    assert row["issue_count"] == 1 and any(i["acked"] for i in row["issues"])
    assert (
        await admin.delete("/api/lifecycle/reconciliation/002100/ack/offboard_biostar_active")
    ).status_code == 204

    # Exports
    csv_resp = await admin.get(
        "/api/lifecycle/reconciliation/export", params={"format": "csv", "only_issues": "true"}
    )
    text = csv_resp.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Mã NV,Họ tên")
    assert "002100" in text and "001500" not in text
    xlsx = await admin.get("/api/lifecycle/reconciliation/export", params={"format": "xlsx"})
    ws = load_workbook(io.BytesIO(xlsx.content)).active
    assert ws["A1"].value == "Mã NV" and ws.max_row == 6

    # Requests list and mapping-driven parsing
    reqs = (await admin.get("/api/lifecycle/requests")).json()
    assert reqs["total"] == 3 and reqs["with_errors"] == 1
    ct = {c["raw"]: c for c in (await admin.get("/api/lifecycle/change-types")).json()}
    assert ct["Chính thức"]["explicit"] == "probation_to_official"
    full = (await admin.get("/api/lifecycle/requests/1001")).json()
    assert full["fields"]["manager_employee_id"] == "001200"
    assert "Mã nhân viên: 003551" in full["description_text"]

    # Document prefill from the RTR request + latest asset snapshot
    pre = (
        await admin.get(
            "/api/lifecycle/documents/prefill", params={"kind": "return", "employee_id": "002100"}
        )
    ).json()
    assert pre["employee"]["full_name"] == "Phạm Thị Bình" and pre["request_id"] == "1002"
    assert pre["employee"]["department"].startswith("Kế Toán")
    assert [a["tag"] for a in pre["assets"]] == ["LT0123"] and pre["assets"][0]["condition"] == "good"
    missing = await admin.get(
        "/api/lifecycle/documents/prefill", params={"kind": "handover", "employee_id": "777777"}
    )
    assert missing.status_code == 404

    # Second sync: unchanged requests are not fetched again
    calls = routes["detail"].call_count
    summary = await _sync()
    assert summary["requests_fetched"] == 0 and routes["detail"].call_count == calls


@respx.mock(assert_all_called=False)
async def test_partial_sync_when_biostar_down(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]
    mock_systems(respx_mock)
    respx_mock.post(f"{BS}/api/login").mock(
        return_value=httpx.Response(401, json={"Response": {"message": "Login failed"}})
    )
    summary = await _sync()
    assert summary["status"] == "partial"
    status = (await admin.get("/api/lifecycle/sync")).json()
    assert "Login failed" in status["runs"][0]["errors"]["biostar"]
    data = (await admin.get("/api/lifecycle/reconciliation")).json()
    row = next(r for r in data["items"] if r["employee_id"] == "002100")
    assert row["cells"]["biostar"]["status"] == "skipped"
    assert "offboard_biostar_active" not in {i["rule"] for i in row["issues"]}


@respx.mock(assert_all_called=False)
async def test_sync_now_runs_through_scheduler(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]
    from app.core.modules.registry import get_registry
    from app.core.scheduler.service import run_once

    mock_systems(respx_mock)
    resp = await admin.post("/api/lifecycle/sync")
    assert resp.status_code == 200
    # Other due tasks (e.g. core.workflow woken by job actions) may run first.
    ran = [await run_once(get_sessionmaker(), get_registry()) for _ in range(3)]
    assert "lifecycle.sync" in ran
    task = (await admin.get("/api/lifecycle/sync")).json()["task"]
    assert task["last_status"] == "success" and task["running"] is False
    assert task["last_result"]["status"] in ("success", "partial")
    run = (await admin.get("/api/lifecycle/sync")).json()["runs"][0]
    assert run["trigger"] == "manual" and run["requested_by"] == "admin@test.local"
    # The sync task is not due again right away
    assert "lifecycle.sync" not in [await run_once(get_sessionmaker(), get_registry()) for _ in range(2)]


@respx.mock(assert_all_called=False)
async def test_allocation_plan_and_band_override(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]
    mock_systems(respx_mock)
    await _sync()
    data = (await admin.get("/api/lifecycle/allocation")).json()
    req = next(r for r in data["requests"] if r["employee_id"] == "003551")
    assert req["band"] == "B3" and req["band_source"] == "rtr" and req["job"]["status"] == "draft"
    lines = {ln["label"]: ln for ln in req["lines"]}
    assert [a["id"] for a in lines["Màn hình"]["reuse"]] == ["a2"]  # In Store monitor reused
    assert lines["Laptop"]["buy"] == 1 and req["status"] == "buy"
    stock = {s["label"]: s for s in data["stock"]}
    assert stock["Màn hình"]["in_store"] == 1 and stock["Laptop"]["to_buy"] >= 1
    # Devices outside the standard still get a stock card (Workstation shown as "PC").
    assert (
        stock["PC"]["in_store"] == 1 and not stock["PC"]["standard"] and stock["PC"]["items"][0]["id"] == "a4"
    )
    # Stock detail: the In Store monitor, set aside for the onboard that reuses it.
    [monitor] = stock["Màn hình"]["items"]
    assert monitor["id"] == "a2" and monitor["reserved_for"] == {
        "name": req["full_name"],
        "display_id": req["display_id"],
        "chosen": False,  # suggested, not on the job yet
    }
    # Planned counts only assets still in stock, so in_store = free + planned exactly.
    assert stock["Màn hình"]["planned"] == 1 and stock["Màn hình"]["chosen"] == 0
    assert data["no_band"] == sum(r["status"] == "no_band" for r in data["requests"])
    assert "serial" in monitor and stock["Laptop"]["items"] == []

    # The preparation wizard gets everything pre-filled: RTR details, SDP user by employee ID, devices.
    prep = (await admin.get(f"/api/lifecycle/jobs/{req['job']['id']}/prepare")).json()
    assert prep["request"]["employee_id"] == "003551" and prep["request"]["band"] == "B3"
    assert prep["sdp_user"]["id"] == "u1"
    assert {d["label"]: d["value"] for d in prep["request"]["details"]}["Phòng"] == "Kinh Doanh"
    assert [
        a["id"] for a in {ln["label"]: ln for ln in prep["allocation"]["lines"]}["Màn hình"]["reuse"]
    ] == ["a2"]

    r = await admin.put(f"/api/lifecycle/requests/{req['id']}/band", json={"band": "B5"})
    assert r.json() == {"band": "B5", "band_source": "override"}
    r = await admin.put(f"/api/lifecycle/requests/{req['id']}/band", json={"band": ""})
    assert r.json()["band_source"] == "rtr"


async def test_settings_validation_and_permissions(admin, client) -> None:  # type: ignore[no-untyped-def]
    bad = await admin.put("/api/lifecycle/settings", json={"rtr_lookback_days": 1})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_settings"
    await client.login("viewer-lc@test.local")
    assert (await client.get("/api/lifecycle/reconciliation")).status_code == 403


@respx.mock(assert_all_called=False)
async def test_completed_onboard_leaves_allocation(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]
    from app.core.workflow.models import Job

    mock_systems(respx_mock)
    await _sync()
    req = next(
        r
        for r in (await admin.get("/api/lifecycle/allocation")).json()["requests"]
        if r["employee_id"] == "003551"
    )
    async with get_sessionmaker()() as s:
        job = await s.get(Job, req["job"]["id"])
        prev = job.status
        job.status = "completed"
        await s.commit()
        try:
            data = (await admin.get("/api/lifecycle/allocation")).json()
            assert "003551" not in {r["employee_id"] for r in data["requests"]}
        finally:
            job.status = prev
            await s.commit()


@respx.mock(assert_all_called=False)
async def test_snapshots_kept_for_latest_runs_only(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import func, select

    from app.modules.lifecycle.models import ReconRow, Snapshot, SyncRun

    mock_systems(respx_mock)
    runs = [(await _sync())["run_id"] for _ in range(3)]
    async with get_sessionmaker()() as s:
        snap_runs = set((await s.scalars(select(Snapshot.run_id).distinct())).all())
        assert {str(r) for r in snap_runs} == set(runs[1:])
        # Reconciliation history keeps the configured retention.
        assert await s.scalar(select(func.count()).select_from(ReconRow).where(ReconRow.run_id == runs[0]))
        assert (await s.get(SyncRun, runs[0])).status == "success"
    detail = (await admin.get("/api/lifecycle/reconciliation/002100")).json()
    assert detail["assets"][0]["asset_tag"] == "LT0123" and detail["biostar"]


@respx.mock(assert_all_called=False)
async def test_sync_crash_marks_run_failed(admin, configured, respx_mock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.modules.lifecycle import sync
    from app.modules.lifecycle.models import SyncRun

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("reconcile exploded")

    mock_systems(respx_mock)
    monkeypatch.setattr(sync, "reconcile", boom)
    with pytest.raises(RuntimeError):
        await _sync()
    async with get_sessionmaker()() as s:
        run = await s.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
    assert run.status == "failure" and run.finished_at is not None
    assert "reconcile exploded" in run.errors["crash"]


BS_OFFICE = "https://bs-office.test.local"


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_second_biostar_matched_on_last_six_digits(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]
    # Office BioStar: employee 003551 is user "1003551" (7 digits; SDP matches on the last 6).
    mock_systems(respx_mock)
    respx_mock.post(f"{BS_OFFICE}/api/login").mock(
        return_value=httpx.Response(200, headers={"bs-session-id": "o"}, json={"User": {}})
    )
    office = [{"user_id": "1003551", "name": "Office Person", "disabled": "false"}]
    respx_mock.get(f"{BS_OFFICE}/api/users").mock(
        return_value=httpx.Response(200, json={"UserCollection": {"total": "1", "rows": office}})
    )
    respx_mock.post(f"{BS_OFFICE}/api/logout").mock(return_value=httpx.Response(200, json={}))
    prof = (
        await admin.post(
            "/api/core/connections",
            json={
                "system_type": "biostar",
                "name": "BS Office",
                "base_url": BS_OFFICE,
                "config": {"verify_tls": False},
            },
        )
    ).json()
    await admin.put(
        f"/api/core/connections/{prof['id']}/credentials/read",
        json={"auth_type": "username_password", "public": {"username": "r"}, "secrets": {"password": "p"}},
    )
    values = (await admin.get("/api/lifecycle/settings")).json()["values"]
    values["biostar_sources"] = [{"profile_id": prof["id"]}]
    values["biostar_retired_groups"] = ["kế toán"]  # 002100 is in group "Kế Toán" (case-insensitive)
    assert (await admin.put("/api/lifecycle/settings", json=values)).status_code == 200

    summary = await _sync()
    assert summary["status"] == "success", summary
    rows = {r["employee_id"]: r for r in (await admin.get("/api/lifecycle/reconciliation")).json()["items"]}
    assert "1003551" not in rows  # consolidated under the SDP employee ID
    assert rows["003551"]["cells"]["biostar"]["label"] == "Hoạt động"
    assert "BS Office (user 1003551)" in rows["003551"]["cells"]["biostar"]["detail"]
    assert rows["002100"]["cells"]["biostar"]["label"] == "Nghỉ việc"

    # "Tự động" main must skip a connection listed as an extra source: here BS E2E (created first) is the extra
    # source, so the automatic main is BS Office and both systems are still read.
    values = (await admin.get("/api/lifecycle/settings")).json()["values"]
    values["biostar_profile_id"] = None
    values["biostar_sources"] = [{"profile_id": configured[1]["id"]}]
    assert (await admin.put("/api/lifecycle/settings", json=values)).status_code == 200
    await _sync()
    rows = {r["employee_id"]: r for r in (await admin.get("/api/lifecycle/reconciliation")).json()["items"]}
    # BS Office read as the (automatic) main system: its IDs are matched on the last 6 digits too.
    assert "BS Office (user 1003551)" in rows["003551"]["cells"]["biostar"]["detail"]
    assert "BS E2E (user 002100)" in rows["002100"]["cells"]["biostar"]["detail"]
    values["biostar_profile_id"] = configured[1]["id"]
    values["biostar_sources"] = [{"profile_id": prof["id"]}]
    assert (await admin.put("/api/lifecycle/settings", json=values)).status_code == 200

    # One BioStar unreadable → no BioStar judgement at all (never judge a system whose read failed).
    respx_mock.get(f"{BS_OFFICE}/api/users").mock(return_value=httpx.Response(500, json={}))
    summary = await _sync()
    rows = {r["employee_id"]: r for r in (await admin.get("/api/lifecycle/reconciliation")).json()["items"]}
    assert rows["003551"]["cells"]["biostar"]["status"] == "skipped"


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_stalled_job_goes_to_the_coordinator_not_the_operator(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.core.workflow.models import Job

    mock_systems(respx_mock)
    await _sync()
    me = (await admin.get("/api/core/me")).json()
    async with get_sessionmaker()() as s:
        job = (await s.scalars(select(Job).where(Job.workflow_key == "lifecycle.onboard"))).first()
        assert job is not None
        job.status, job.assignee_user_id = "running", me["id"]
        job.params = {**job.params, "effective_date": "2020-01-01"}  # long past
        job.updated_at = datetime.now(UTC) - timedelta(hours=3)
        await s.commit()
    items = {i["key"]: i for i in (await admin.get("/api/core/me/next")).json()["items"]}
    # As assignee: shown apart, not as work. As coordinator (job.assign): one actionable "kiểm tra worker".
    assert items[f"job:{job.id}"]["passive"] is True
    assert (
        items["jobs:stalled"]["priority"] == 1
        and items["jobs:stalled"]["href"] == "/lifecycle/jobs?status=needs"
    )
    late = {i["key"]: i for i in (await admin.get("/api/core/me/summary")).json()["items"]}["jobs:late"]
    assert (
        late["count"] == 0 and "assignee=me" in late["href"]
    )  # waiting on the system is not the person's lateness
