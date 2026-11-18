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
from tests.lifecycle_fixtures import ASSETS, BIOSTAR_USERS, DETAILS, REQUESTS, SDP_USERS, sdp_ok

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
    # Users come back in two pages to exercise pagination.
    routes["users"] = router.get(f"{SDP}/api/v3/users").mock(
        side_effect=[
            httpx.Response(200, json=sdp_ok("users", SDP_USERS[:2], has_more=True)),
            httpx.Response(200, json=sdp_ok("users", SDP_USERS[2:])),
        ]
        * 10
    )
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
    summary = await _sync()
    assert summary["status"] == "success", summary
    assert summary["requests_fetched"] == 3 and summary["sdp_users"] == 3 and summary["biostar_users"] == 3
    assert summary["assets"] == 2  # printer filtered out by product types
    assert summary["jobs_created"] == 2  # onboard 003551 + offboard 002100 (1003 has parse errors)
    jobs = (await admin.get("/api/core/jobs", params={"status": "draft"})).json()["items"]
    assert {(j["workflow_key"], j["subject_key"]) for j in jobs} >= {("lifecycle.onboard", "003551"), ("lifecycle.offboard", "002100")}
    assert routes["list"].calls[0].request.headers["authtoken"] == "k-e2e-0000000001"
    criteria = json.loads(routes["list"].calls[0].request.url.params["input_data"])["list_info"][
        "search_criteria"
    ]
    assert criteria[0] == {"field": "requester.name", "condition": "is", "value": "RTR"}

    data = (await admin.get("/api/lifecycle/reconciliation")).json()
    by_emp = {r["employee_id"]: r for r in data["items"]}
    assert set(by_emp) == {"003551", "002100", "001500", "009999"}
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
    assert ws["A1"].value == "Mã NV" and ws.max_row == 5

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


async def test_settings_validation_and_permissions(admin, client) -> None:  # type: ignore[no-untyped-def]
    bad = await admin.put("/api/lifecycle/settings", json={"rtr_lookback_days": 1})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_settings"
    await client.login("viewer-lc@test.local")
    assert (await client.get("/api/lifecycle/reconciliation")).status_code == 403
