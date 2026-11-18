"""Onboard / Offboard workflows end-to-end (engine + API) against stateful SDP / BioStar mocks."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.audit.models import AuditLog
from app.core.db import get_sessionmaker
from app.core.workflow.engine import pump, today_vn
from app.modules.lifecycle.models import LifecycleRequest

SDP = "https://sdp-wf.test.local"
BS = "https://bs-wf.test.local"
OK = {"response_status": {"status_code": 2000, "status": "success"}}
needs_soffice = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")


class FakeSystems:
    def __init__(self) -> None:
        self.requests = {"9101": "Open", "9102": "Open"}
        self.users = [
            {"id": "u1", "name": "Nguyễn Văn An", "employee_id": "003551"},
            {"id": "u2", "name": "Phạm Thị Bình", "employee_id": "002100"},
        ]
        self.assets = {
            "5001": {
                "id": "5001",
                "name": "HSC-LT-5001",
                "asset_tag": "LT5001",
                "state": {"name": "In Store"},
                "user": None,
                "product": {"name": "Dell", "product_type": {"name": "Laptop"}},
            },
            "5002": {
                "id": "5002",
                "name": "HSC-LT-5002",
                "asset_tag": "LT5002",
                "state": {"name": "In Use"},
                "user": {"id": "u2", "name": "Phạm Thị Bình"},
                "product": {"name": "Dell", "product_type": {"name": "Laptop"}},
            },
        }
        self.bs_users: dict[str, dict] = {
            "002100": {
                "user_id": "002100",
                "name": "Pham Thi Binh",
                "disabled": "false",
                "access_groups": [{"id": "1", "name": "HCM"}],
                "card_count": "1",
            }
        }
        self.calls: list[str] = []

    def install(self, r: respx.MockRouter) -> None:
        def req_detail(request: httpx.Request, rid: str) -> httpx.Response:
            if request.method == "PUT":
                self.requests[rid] = json.loads(parse_qs(request.content.decode())["input_data"][0])[
                    "request"
                ]["status"]["name"]
                self.calls.append(f"status:{rid}:{self.requests[rid]}")
                return httpx.Response(200, json=OK)
            return httpx.Response(
                200, json={**OK, "request": {"id": rid, "status": {"name": self.requests[rid]}}}
            )

        r.route(url__regex=rf"{re.escape(SDP)}/api/v3/requests/(?P<rid>\d+)$").mock(side_effect=req_detail)
        r.post(url__regex=rf"{re.escape(SDP)}/api/v3/requests/\d+/notes$").mock(
            side_effect=lambda req: (self.calls.append("note"), httpx.Response(200, json=OK))[1]
        )
        r.post(url__regex=rf"{re.escape(SDP)}/api/v3/requests/\d+/_uploads$").mock(
            side_effect=lambda req: (self.calls.append("upload"), httpx.Response(200, json=OK))[1]
        )

        def users(request: httpx.Request) -> httpx.Response:
            crit = json.loads(request.url.params["input_data"])["list_info"]["search_criteria"]
            rows = [u for u in self.users if u["employee_id"] == crit["value"]]
            return httpx.Response(200, json={**OK, "list_info": {"has_more_rows": False}, "users": rows})

        r.get(f"{SDP}/api/v3/users").mock(side_effect=users)

        def asset(request: httpx.Request, aid: str) -> httpx.Response:
            if request.method == "PUT":
                body = json.loads(parse_qs(request.content.decode())["input_data"][0])["asset"]
                self.assets[aid]["state"] = body["state"]
                self.assets[aid]["user"] = body["user"]
                self.calls.append(f"asset:{aid}:{body['state']['name']}")
            return httpx.Response(200, json={**OK, "asset": self.assets[aid]})

        r.route(url__regex=rf"{re.escape(SDP)}/api/v3/assets/(?P<aid>\d+)$").mock(side_effect=asset)

        def assets_of(request: httpx.Request) -> httpx.Response:
            crit = json.loads(request.url.params["input_data"])["list_info"]["search_criteria"]
            rows = [a for a in self.assets.values() if (a.get("user") or {}).get("id") == crit["value"]]
            return httpx.Response(200, json={**OK, "list_info": {"has_more_rows": False}, "assets": rows})

        r.get(f"{SDP}/api/v3/assets").mock(side_effect=assets_of)

        r.post(f"{BS}/api/login").mock(
            return_value=httpx.Response(200, headers={"bs-session-id": "s"}, json={"User": {}})
        )
        r.post(f"{BS}/api/logout").mock(return_value=httpx.Response(200, json={}))

        def bs_user(request: httpx.Request, uid: str) -> httpx.Response:
            if request.method == "PUT":
                self.bs_users[uid].update(json.loads(request.content)["User"])
                self.calls.append(f"bs-update:{uid}")
                return httpx.Response(200, json={"Response": {"code": "0"}})
            if uid not in self.bs_users:
                return httpx.Response(404, json={"Response": {"code": "1", "message": "not found"}})
            return httpx.Response(200, json={"User": self.bs_users[uid]})

        r.route(url__regex=rf"{re.escape(BS)}/api/users/(?P<uid>\w+)$").mock(side_effect=bs_user)

        def bs_create(request: httpx.Request) -> httpx.Response:
            u = json.loads(request.content)["User"]
            self.bs_users[u["user_id"]] = {**u, "card_count": "0"}
            self.calls.append(f"bs-create:{u['user_id']}")
            return httpx.Response(200, json={"Response": {"code": "0"}})

        r.post(f"{BS}/api/users").mock(side_effect=bs_create)


@pytest.fixture
async def systems(admin, respx_mock):  # type: ignore[no-untyped-def]
    fake = FakeSystems()
    fake.install(respx_mock)
    existing = {p["name"]: p for p in (await admin.get("/api/core/connections")).json()}
    sdp = (
        existing.get("SDP WF")
        or (
            await admin.post(
                "/api/core/connections",
                json={"system_type": "sdp", "name": "SDP WF", "base_url": SDP, "config": {}},
            )
        ).json()
    )
    bs = (
        existing.get("BS WF")
        or (
            await admin.post(
                "/api/core/connections",
                json={"system_type": "biostar", "name": "BS WF", "base_url": BS, "config": {}},
            )
        ).json()
    )
    for purpose in ("read", "write"):
        await admin.put(
            f"/api/core/connections/{sdp['id']}/credentials/{purpose}",
            json={"auth_type": "api_key", "secrets": {"api_key": f"k-{purpose}-00000000"}},
        )
        await admin.put(
            f"/api/core/connections/{bs['id']}/credentials/{purpose}",
            json={
                "auth_type": "username_password",
                "public": {"username": purpose},
                "secrets": {"password": "p"},
            },
        )
    values = (await admin.get("/api/lifecycle/settings")).json()["values"]
    await admin.put(
        "/api/lifecycle/settings",
        json={
            **values,
            "sdp_profile_id": sdp["id"],
            "biostar_profile_id": bs["id"],
            "biostar_access_group_ids": ["7"],
            "reviewer_name": "Trần Văn Quản",
        },
    )
    async with get_sessionmaker()() as s:
        for rid, kind, emp, name in (
            ("9101", "onboard", "003551", "Nguyễn Văn An"),
            ("9102", "offboard", "002100", "Phạm Thị Bình"),
        ):
            # (Re)write after saving settings: saving re-parses descriptions, which are empty here.
            row = await s.get(LifecycleRequest, rid) or LifecycleRequest(id=rid)
            row.display_id, row.subject, row.employee_id, row.full_name = (
                f"79{rid[-2:]}",
                f"RTR {name}",
                emp,
                name,
            )
            row.kind, row.effective_date, row.fields, row.parse_errors = (
                kind,
                date(2026, 11, 10),
                {"department": "Kinh Doanh"},
                [],
            )
            row.description_text = ""
            s.add(row)
        await s.commit()
    return fake


async def run_worker() -> None:
    async with get_sessionmaker()() as s:
        await pump(s)


async def job(admin, job_id: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    return (await admin.get(f"/api/core/jobs/{job_id}")).json()


def step(j: dict, key: str) -> dict:  # type: ignore[type-arg]
    return next(s for s in j["steps"] if s["key"] == key)


@needs_soffice
@respx.mock(assert_all_called=False)
async def test_onboard_end_to_end(admin, systems) -> None:  # type: ignore[no-untyped-def]
    created = await admin.post("/api/lifecycle/jobs", json={"request_id": "9101"})
    assert created.status_code == 201, created.text
    j = created.json()
    assert (
        j["status"] == "draft" and j["workflow_key"] == "lifecycle.onboard" and j["subject_key"] == "003551"
    )
    again = (await admin.post("/api/lifecycle/jobs", json={"request_id": "9101"})).json()
    assert again["id"] == j["id"]  # idempotent per request

    j = (
        await admin.put(
            f"/api/lifecycle/jobs/{j['id']}/params",
            json={
                "version": j["version"],
                "assets": [{"id": "5001", "name": "HSC-LT-5001", "tag": "LT5001", "product_type": "Laptop"}],
            },
        )
    ).json()
    preview = (await admin.get(f"/api/core/jobs/{j['id']}/preview")).json()
    assert any("HSC-LT-5001" in line for p in preview for line in p["lines"])
    assert systems.calls == []  # preview never writes

    started = await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})
    assert started.status_code == 200, started.text
    j = started.json()
    assert j["status"] == "running"
    await run_worker()
    j = await job(admin, j["id"])
    assert [s["status"] for s in j["steps"]][:5] == ["done", "done", "done", "done", "waiting"]
    assert systems.assets["5001"]["state"]["name"] == "In Use" and systems.assets["5001"]["user"] == {
        "id": "u1"
    }
    assert systems.bs_users["003551"]["access_groups"] == [{"id": "7"}]
    assert step(j, "assign_assets")["before_state"]["assets"]["5001"]["state"] == "In Store"
    assert j["status"] == "waiting" and j["current_step"] == "biostar_enroll"

    # Manual step verifies BioStar: no credential yet → refused, then enrolled → accepted
    refused = await admin.post(f"/api/core/jobs/{j['id']}/steps/biostar_enroll/complete", json={"data": {}})
    assert refused.json()["error"]["code"] == "manual_incomplete"
    systems.bs_users["003551"]["fingerprint_template_count"] = "2"
    ok = await admin.post(f"/api/core/jobs/{j['id']}/steps/biostar_enroll/complete", json={"data": {}})
    assert ok.status_code == 200, ok.text
    assert step(ok.json(), "biostar_enroll")["status"] == "done"

    await run_worker()
    j = await job(admin, j["id"])
    doc_step = step(j, "handover_doc")
    assert doc_step["status"] == "waiting" and doc_step["output"]["document_id"]
    doc_id = doc_step["output"]["document_id"]
    doc = (await admin.get(f"/api/core/documents/{doc_id}")).json()
    assert doc["data"]["assets"][0]["tag"] == "LT5001" and doc["data"]["reviewer"] == "Trần Văn Quản"
    # Not signed yet → job cannot finish
    await run_worker()
    assert (await job(admin, j["id"]))["status"] == "waiting"

    await admin.post(f"/api/core/documents/{doc_id}/finalize")
    await admin.post(
        f"/api/core/documents/{doc_id}/signed",
        files={"file": ("s.pdf", b"%PDF-1.4\n%signed\n", "application/pdf")},
    )
    async with get_sessionmaker()() as s:  # the document step re-checks every 60 s; make it due now
        from app.core.workflow.models import JobStep

        st = await s.scalar(select(JobStep).where(JobStep.key == "handover_doc", JobStep.job_id == j["id"]))
        st.next_run_at = None
        await s.commit()
    await run_worker()
    j = await job(admin, j["id"])
    assert j["status"] == "completed", [(s["key"], s["status"], s["message"]) for s in j["steps"]]
    assert systems.calls[-3:] == ["upload", "note", "status:9101:Resolved"]

    async with get_sessionmaker()() as s:
        actions = (await s.scalars(select(AuditLog.action).where(AuditLog.target_id == j["id"]))).all()
    assert {
        "workflow.job_created",
        "workflow.job_started",
        "workflow.step_executed",
        "workflow.step_completed",
    } <= set(actions)


@respx.mock(assert_all_called=False)
async def test_offboard_schedule_confirm_and_override(admin, client, systems) -> None:  # type: ignore[no-untyped-def]
    resp = await admin.post("/api/lifecycle/jobs", json={"request_id": "9102"})
    assert resp.status_code == 201, resp.text
    j = resp.json()
    assert [s["key"] for s in j["steps"]] == [
        "check_request",
        "find_sdp_user",
        "collect_assets",
        "return_assets",
        "biostar_offboard",
        "return_doc",
        "close_request",
    ]
    # Schedule for tomorrow: the worker leaves it alone
    tomorrow = (today_vn() + timedelta(days=1)).isoformat()
    j = (
        await admin.post(
            f"/api/core/jobs/{j['id']}/start", json={"version": j["version"], "scheduled_for": tomorrow}
        )
    ).json()
    assert j["status"] == "scheduled"
    await run_worker()
    assert (await job(admin, j["id"]))["status"] == "scheduled"
    # Start now instead
    j = (
        await admin.post(
            f"/api/core/jobs/{j['id']}/start", json={"version": (await job(admin, j["id"]))["version"]}
        )
    ).json()
    await run_worker()
    j = await job(admin, j["id"])
    collect = step(j, "collect_assets")
    assert collect["status"] == "waiting" and [a["id"] for a in collect["output"]["assets"]] == ["5002"]

    incomplete = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/collect_assets/complete", json={"data": {"assets": []}}
    )
    assert incomplete.json()["error"]["code"] == "manual_incomplete"
    await admin.post(
        f"/api/core/jobs/{j['id']}/steps/collect_assets/complete",
        json={"data": {"assets": [{"id": "5002", "condition": "missing_accessory", "note": "Thiếu sạc"}]}},
    )
    await run_worker()
    j = await job(admin, j["id"])
    assert systems.assets["5002"]["state"]["name"] == "In Store" and systems.assets["5002"]["user"] is None
    bs_step = step(j, "biostar_offboard")
    assert bs_step["status"] == "waiting" and bs_step["requires_confirm"]
    assert systems.bs_users["002100"]["disabled"] == "false"  # not touched before confirmation

    wrong = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/biostar_offboard/confirm", json={"typed": "2100"}
    )
    assert wrong.json()["error"]["code"] == "confirm_mismatch"
    await admin.post(f"/api/core/jobs/{j['id']}/steps/biostar_offboard/confirm", json={"typed": "002100"})
    await run_worker()
    j = await job(admin, j["id"])
    assert (
        systems.bs_users["002100"]["disabled"] == "true" and systems.bs_users["002100"]["access_groups"] == []
    )
    assert step(j, "biostar_offboard")["before_state"]["user"]["access_groups"] == [
        {"id": "1", "name": "HCM"}
    ]
    doc_step = step(j, "return_doc")
    doc = (await admin.get(f"/api/core/documents/{doc_step['output']['document_id']}")).json()
    assert doc["kind"] == "lifecycle.return" and doc["data"]["assets"][0]["condition"] == "missing_accessory"

    not_skippable = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/return_doc/skip", json={"reason": "không cần"}
    )
    assert not_skippable.json()["error"]["code"] == "not_skippable"
    # Helpdesk cannot force-close; admin can, with a reason
    await client.login("hd-wf@test.local")
    assert (
        await client.post(f"/api/core/jobs/{j['id']}/override-close", json={"reason": "thử đóng"})
    ).status_code == 403
    closed = (
        await admin.post(
            f"/api/core/jobs/{j['id']}/override-close", json={"reason": "Nhân viên đã ký giấy, scan sau"}
        )
    ).json()
    assert closed["status"] == "completed" and closed["close_reason"].startswith("Đóng bắt buộc")


@respx.mock(assert_all_called=False)
async def test_closed_request_fails_and_retry_recovers(admin, systems) -> None:  # type: ignore[no-untyped-def]
    systems.requests["9101"] = "Closed"
    async with get_sessionmaker()() as s:
        from app.core.workflow.models import Job

        old = await s.scalar(select(Job).where(Job.idempotency_key == "lifecycle:onboard:9101"))
        if old is not None:
            await s.delete(old)
            await s.commit()
    j = (await admin.post("/api/lifecycle/jobs", json={"request_id": "9101"})).json()
    # Do not reuse an SDP user guessed from an earlier sync: look it up live.
    j = (
        await admin.put(
            f"/api/lifecycle/jobs/{j['id']}/params", json={"version": j["version"], "clear_sdp_user": True}
        )
    ).json()
    assert j["params"]["sdp_user_id"] is None
    await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})
    await run_worker()
    j = await job(admin, j["id"])
    assert j["status"] == "failed" and "Closed" in step(j, "check_request")["message"]
    assert systems.calls == []
    systems.requests["9101"] = "Open"
    await admin.post(f"/api/core/jobs/{j['id']}/steps/check_request/retry")
    systems.users = []  # AD account not yet in SDP → the job waits instead of failing
    await run_worker()
    j = await job(admin, j["id"])
    assert step(j, "check_request")["status"] == "done"
    assert step(j, "find_sdp_user")["status"] == "waiting" and j["status"] == "waiting"
    cancelled = (
        await admin.post(f"/api/core/jobs/{j['id']}/cancel", json={"reason": "Ứng viên không nhận việc"})
    ).json()
    assert cancelled["status"] == "cancelled"


async def test_auto_create_jobs_and_list(admin) -> None:  # type: ignore[no-untyped-def]
    from app.modules.lifecycle.jobs_api import auto_create_jobs

    async with get_sessionmaker()() as s:
        s.add(
            LifecycleRequest(
                id="9199",
                display_id="7999",
                subject="x",
                employee_id="004444",
                full_name="Lê C",
                kind="onboard",
                effective_date=date(2026, 11, 20),
                fields={},
                parse_errors=[],
                description_text="",
            )
        )
        await s.commit()
        created = await auto_create_jobs(s)
        await s.commit()
        assert created >= 1
        assert await auto_create_jobs(s) == 0  # idempotent
    listed = (await admin.get("/api/core/jobs", params={"q": "004444"})).json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "draft"
    assert listed["items"][0]["created_by"] == "system (đồng bộ)"
