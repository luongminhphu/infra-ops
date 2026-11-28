"""Onboard / Offboard workflows end-to-end (engine + API) against stateful SDP / BioStar mocks."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.audit.models import AuditLog
from app.core.audit.service import Actor
from app.core.db import get_sessionmaker
from app.core.workflow.engine import pump, today_vn
from app.core.workflow.models import Job
from app.modules.lifecycle.models import LifecycleRequest

SDP = "https://sdp-wf.test.local"
BS = "https://bs-wf.test.local"
OK = {"response_status": {"status_code": 2000, "status": "success"}}
needs_soffice = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")


class FakeSystems:
    def __init__(self) -> None:
        self.requests = {"9101": "Open", "9102": "Open"}
        self.users = [
            {
                "id": "u1",
                "name": "Nguyễn Văn An",
                "employee_id": "003551",
                "job_title": "Chuyên viên",
                "department": {"name": "Kinh Doanh", "site": {"name": "AB Tower"}},
            },
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
            "5003": {
                "id": "5003",
                "name": "HSC-MN-5003",
                "asset_tag": "MN5003",
                "state": {"name": "In Store"},
                "user": None,
                "product": {"name": "Dell", "product_type": {"name": "Monitor"}},
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
        self.request_tech: dict[str, str] = {}
        self.technicians = [{"id": "71", "name": "Tech 71", "email_id": "op-assign2@test.local"}]
        self.notes: dict[str, list[str]] = {}
        self.attachments: dict[str, list[str]] = {}
        self.lose_response: set[str] = set()  # writes that land on SDP but whose response is lost once
        self.fail_put: set[str] = set()  # asset ids whose next update is refused

    def _maybe_lost(self, kind: str) -> httpx.Response:
        if kind in self.lose_response:
            self.lose_response.discard(kind)
            raise httpx.ReadTimeout("response lost")
        return httpx.Response(200, json=OK)

    def install(self, r: respx.MockRouter) -> None:
        def req_detail(request: httpx.Request, rid: str) -> httpx.Response:
            if request.method == "PUT":
                body = json.loads(parse_qs(request.content.decode())["input_data"][0])["request"]
                if "technician" in body:
                    self.request_tech[rid] = body["technician"]["id"]
                    self.calls.append(f"technician:{rid}:{body['technician']['id']}")
                    return httpx.Response(200, json=OK)
                self.requests[rid] = body["status"]["name"]
                self.calls.append(f"status:{rid}:{self.requests[rid]}")
                return httpx.Response(200, json=OK)
            tech = self.request_tech.get(rid)
            return httpx.Response(
                200,
                json={
                    **OK,
                    "request": {
                        "id": rid,
                        "status": {"name": self.requests[rid]},
                        "technician": {"id": tech, "name": f"Tech {tech}"} if tech else None,
                    },
                },
            )

        r.route(url__regex=rf"{re.escape(SDP)}/api/v3/requests/(?P<rid>\d+)$").mock(side_effect=req_detail)

        def note(request: httpx.Request, rid: str) -> httpx.Response:
            if request.method == "GET":
                rows = [{"description": d} for d in self.notes.get(rid, [])]
                return httpx.Response(200, json={**OK, "list_info": {"has_more_rows": False}, "notes": rows})
            body = json.loads(parse_qs(request.content.decode())["input_data"][0])["note"]
            self.notes.setdefault(rid, []).append(body["description"])
            self.calls.append("note")
            return self._maybe_lost("note")

        def upload(request: httpx.Request, rid: str) -> httpx.Response:
            name = re.search(rb'filename="([^"]+)"', request.content).group(1).decode()  # type: ignore[union-attr]
            self.attachments.setdefault(rid, []).append(name)
            self.calls.append("upload")
            return self._maybe_lost("upload")

        r.route(url__regex=rf"{re.escape(SDP)}/api/v3/requests/(?P<rid>\d+)/notes(\?|$)").mock(
            side_effect=note
        )
        r.post(url__regex=rf"{re.escape(SDP)}/api/v3/requests/(?P<rid>\d+)/_uploads$").mock(
            side_effect=upload
        )
        r.get(url__regex=rf"{re.escape(SDP)}/api/v3/requests/(?P<rid>\d+)/attachments(\?|$)").mock(
            side_effect=lambda req, rid: httpx.Response(
                200,
                json={
                    **OK,
                    "list_info": {"has_more_rows": False},
                    "attachments": [{"name": n} for n in self.attachments.get(rid, [])],
                },
            )
        )

        def users(request: httpx.Request) -> httpx.Response:
            crit = json.loads(request.url.params["input_data"])["list_info"]["search_criteria"]
            rows = [u for u in self.users if u["employee_id"] == crit["value"]]
            return httpx.Response(200, json={**OK, "list_info": {"has_more_rows": False}, "users": rows})

        r.get(f"{SDP}/api/v3/users").mock(side_effect=users)

        def technicians(request: httpx.Request) -> httpx.Response:
            crit = json.loads(request.url.params["input_data"])["list_info"]["search_criteria"]
            rows = [t for t in self.technicians if t["email_id"] == crit["value"]]
            return httpx.Response(
                200, json={**OK, "list_info": {"has_more_rows": False}, "technicians": rows}
            )

        r.get(f"{SDP}/api/v3/technicians").mock(side_effect=technicians)

        def asset(request: httpx.Request, aid: str) -> httpx.Response:
            if request.method == "PUT":
                if aid in self.fail_put:
                    self.fail_put.discard(aid)
                    return httpx.Response(500, json={"response_status": {"status_code": 4000}})
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
    from tests.personal_keys import give_personal_keys

    await give_personal_keys(sdp["id"])  # job steps run with the responsible person's own SDP key
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


async def verify(admin, j: dict, key: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    """Operator check of a performed step: evidence where required, every checklist item ticked."""
    st = step(j, key)
    if st["evidence_required"] and not st["evidence"]:
        r = await admin.post(
            f"/api/core/jobs/{j['id']}/steps/{key}/evidence",
            json={"kind": "note", "text": "Đã kiểm tra trên hệ thống"},
        )
        assert r.status_code == 201, r.text
    r = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/{key}/verify", json={"checked": [i["id"] for i in st["checklist"]]}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def settle(admin, job_id: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    """Run the worker and verify every performed step until the job needs something else."""
    for _ in range(20):
        await run_worker()
        j = await job(admin, job_id)
        pending = [s for s in j["steps"] if s["status"] == "verify"]
        if not pending:
            return j
        await verify(admin, j, pending[0]["key"])
    raise AssertionError("job did not settle")


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
                "assets": [
                    {"id": "5001", "name": "HSC-LT-5001", "tag": "LT5001", "product_type": "Laptop"},
                    {"id": "5003", "name": "HSC-MN-5003", "tag": "MN5003", "product_type": "Monitor"},
                ],
            },
        )
    ).json()
    preview = (await admin.get(f"/api/core/jobs/{j['id']}/preview")).json()
    assert any("HSC-LT-5001" in line for p in preview for line in p["lines"])
    assert systems.calls == []  # preview never writes

    started = await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})
    assert started.status_code == 200, started.text
    j = started.json()
    assert j["status"] == "running" and j["checklist_version"] == 0
    await run_worker()
    j = await job(admin, j["id"])
    # Each performed step waits for an operator check before the next one runs.
    assert [s["status"] for s in j["steps"]][:2] == ["verify", "pending"] and j["status"] == "waiting"
    await run_worker()
    assert step(await job(admin, j["id"]), "find_sdp_user")["status"] == "pending"
    half = await admin.post(f"/api/core/jobs/{j['id']}/steps/check_request/verify", json={"checked": ["c1"]})
    assert half.json()["error"]["code"] == "checklist_incomplete"
    j = await verify(admin, j, "check_request")
    assert step(j, "check_request")["verified_by"] == "admin@test.local"
    # Rejecting a check fails the step; retry re-runs it (read-before-write) and asks again.
    await run_worker()
    j = await job(admin, j["id"])
    rej = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/find_sdp_user/verify", json={"ok": False, "note": "Sai user"}
    )
    assert step(rej.json(), "find_sdp_user")["status"] == "failed" and rej.json()["status"] == "failed"
    await admin.post(f"/api/core/jobs/{j['id']}/steps/find_sdp_user/retry")
    await run_worker()
    j = await verify(admin, await job(admin, j["id"]), "find_sdp_user")
    # Partial failure (5001 assigned, 5003 refused): the retry keeps the prior state of both assets.
    systems.fail_put = {"5003"}
    await run_worker()
    failed = step(await job(admin, j["id"]), "assign_assets")
    assert failed["status"] == "failed" and set(failed["before_state"]["assets"]) == {"5001", "5003"}
    await admin.post(f"/api/core/jobs/{j['id']}/steps/assign_assets/retry")
    # assign_assets requires evidence: refused without, accepted with a screenshot.
    await run_worker()
    j = await job(admin, j["id"])
    a = step(j, "assign_assets")
    assert a["status"] == "verify" and a["evidence_required"]
    no_ev = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/assign_assets/verify",
        json={"checked": [i["id"] for i in a["checklist"]]},
    )
    assert no_ev.json()["error"]["code"] == "evidence_required"
    bad = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/assign_assets/evidence/file",
        files={"file": ("x.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert bad.json()["error"]["code"] == "unsupported_file"
    png = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/assign_assets/evidence/file",
        files={"file": ("sdp.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
    )
    assert png.status_code == 201, png.text
    ev = step(png.json(), "assign_assets")["evidence"][0]
    assert ev["kind"] == "image" and ev["filename"] == "sdp.png"
    f = await admin.get(f"/api/core/jobs/{j['id']}/evidence/{ev['id']}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "image/png"
    assert (
        await admin.post(
            f"/api/core/jobs/{j['id']}/steps/assign_assets/evidence",
            json={"kind": "link", "url": "javascript:x"},
        )
    ).json()["error"]["code"] == "invalid_link"
    j = await verify(admin, png.json(), "assign_assets")
    late = await admin.delete(f"/api/core/jobs/{j['id']}/evidence/{ev['id']}")
    assert late.json()["error"]["code"] == "step_finished"
    j = await settle(admin, j["id"])
    assert [s["status"] for s in j["steps"]][:5] == ["done", "done", "done", "done", "waiting"]
    assert systems.assets["5001"]["state"]["name"] == "In Use" and systems.assets["5001"]["user"] == {
        "id": "u1"
    }
    assert systems.bs_users["003551"]["access_groups"] == [{"id": "7"}]
    before = step(j, "assign_assets")["before_state"]["assets"]
    assert before["5001"]["state"] == "In Store" and before["5003"]["state"] == "In Store"
    assert systems.assets["5003"]["state"]["name"] == "In Use"
    assert j["status"] == "waiting" and j["current_step"] == "biostar_enroll"

    # Manual step verifies BioStar: no credential yet → refused, then enrolled → accepted
    refused = await admin.post(f"/api/core/jobs/{j['id']}/steps/biostar_enroll/complete", json={"data": {}})
    assert refused.json()["error"]["code"] == "manual_incomplete"
    systems.bs_users["003551"]["fingerprint_template_count"] = "2"
    ok = await admin.post(f"/api/core/jobs/{j['id']}/steps/biostar_enroll/complete", json={"data": {}})
    assert ok.status_code == 200, ok.text
    assert step(ok.json(), "biostar_enroll")["status"] == "verify"

    j = await settle(admin, j["id"])
    doc_step = step(j, "handover_doc")
    assert doc_step["status"] == "waiting" and doc_step["output"]["document_id"]
    doc_id = doc_step["output"]["document_id"]
    doc = (await admin.get(f"/api/core/documents/{doc_id}")).json()
    assert doc["data"]["assets"][0]["tag"] == "LT5001" and doc["data"]["reviewer"] == "Trần Văn Quản"
    # Job title and work location come from the SDP user found by the job.
    emp = doc["data"]["employee"]
    assert (emp["job_title"], emp["work_location"]) == ("Chuyên viên", "AB Tower")
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
    # The upload, then the note, land on SDP but their responses are lost: retries read first, never repost.
    systems.lose_response = {"upload", "note"}
    j = await settle(admin, j["id"])
    for _ in range(2):
        assert step(j, "close_request")["status"] == "failed", step(j, "close_request")
        await admin.post(f"/api/core/jobs/{j['id']}/steps/close_request/retry")
        j = await settle(admin, j["id"])
    assert j["status"] == "completed", [
        (s["key"], s["status"], s["message"]) for s in j["steps"] if s["status"] != "done"
    ]
    assert systems.calls[-3:] == ["upload", "note", "status:9101:Resolved"]
    assert systems.calls.count("upload") == 1 and systems.calls.count("note") == 1
    assert step(j, "close_request")["output"]["attached"]

    async with get_sessionmaker()() as s:
        actions = (await s.scalars(select(AuditLog.action).where(AuditLog.target_id == j["id"]))).all()
    assert {
        "workflow.job_created",
        "workflow.job_started",
        "workflow.step_executed",
        "workflow.step_completed",
        "workflow.step_verified",
        "workflow.step_rejected",
        "workflow.evidence_added",
    } <= set(actions)


@respx.mock(assert_all_called=False)
async def test_offboard_schedule_confirm_and_override(admin, client, systems) -> None:  # type: ignore[no-untyped-def]
    resp = await admin.post("/api/lifecycle/jobs", json={"request_id": "9102"})
    assert resp.status_code == 201, resp.text
    j = resp.json()
    assert [s["key"] for s in j["steps"]] == [
        "check_request",
        "biostar_offboard",  # access revoked first, before anything else
        "find_sdp_user",
        "collect_assets",
        "return_assets",
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
    j = await settle(admin, j["id"])
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
    assert step(j, "biostar_offboard")["status"] == "verify"
    skip = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/biostar_offboard/skip", json={"reason": "bỏ qua"}
    )
    assert skip.status_code == 409
    j = await settle(admin, j["id"])
    assert (
        systems.bs_users["002100"]["disabled"] == "true" and systems.bs_users["002100"]["access_groups"] == []
    )
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
    j = await settle(admin, j["id"])
    assert systems.assets["5002"]["state"]["name"] == "In Store" and systems.assets["5002"]["user"] is None
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
    # Infra Support cannot force-close; admin can, with a reason
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
    j = await settle(admin, j["id"])
    assert step(j, "check_request")["status"] == "done"
    assert step(j, "find_sdp_user")["status"] == "waiting" and j["status"] == "waiting"
    cancelled = (
        await admin.post(f"/api/core/jobs/{j['id']}/cancel", json={"reason": "Ứng viên không nhận việc"})
    ).json()
    assert cancelled["status"] == "cancelled"
    # Nothing was written (only read-only checks ran): the case goes back to draft, steps reset.
    reopened = await admin.post(f"/api/core/jobs/{j['id']}/reopen", json={"reason": "Chưa thực hiện"})
    assert reopened.status_code == 200, reopened.text
    r = reopened.json()
    assert r["status"] == "draft" and all(x["status"] == "pending" for x in r["steps"])
    again = await admin.post(f"/api/core/jobs/{j['id']}/reopen", json={"reason": "Mở lại để thử"})
    assert again.json()["error"]["code"] == "not_cancelled"


async def test_reopen_refused_after_a_write_step(admin) -> None:  # type: ignore[no-untyped-def]
    from app.core.workflow.models import Job as JobRow

    async with get_sessionmaker()() as s:
        s.add(
            LifecycleRequest(
                id="9196",
                display_id="7996",
                subject="x",
                employee_id="004446",
                full_name="Lê E",
                kind="onboard",
                effective_date=date(2026, 11, 20),
                fields={},
                parse_errors=[],
                description_text="",
            )
        )
        await s.commit()
    j = (await admin.post("/api/lifecycle/jobs", json={"request_id": "9196"})).json()
    await admin.post(f"/api/core/jobs/{j['id']}/cancel", json={"reason": "Huỷ để thử"})
    async with get_sessionmaker()() as s:
        row = await s.get(JobRow, uuid.UUID(j["id"]))
        assign = next(x for x in row.steps if x.key == "assign_assets")
        assign.status, assign.before_state = "done", {"assets": {}}
        await s.commit()
    refused = await admin.post(f"/api/core/jobs/{j['id']}/reopen", json={"reason": "Mở lại để thử"})
    assert refused.json()["error"]["code"] == "job_has_work"


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
        # Already closed in SDP: no draft job.
        s.add(
            LifecycleRequest(
                id="9198",
                display_id="7998",
                subject="x",
                employee_id="004445",
                full_name="Lê D",
                kind="onboard",
                status="resolved",
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
    assert (await admin.get("/api/core/jobs", params={"q": "004445"})).json()["total"] == 0

    # Lean list: same job, no heavy step fields, no assignments; default payload keeps them.
    lean = (await admin.get("/api/core/jobs", params={"q": "004444", "lean": 1})).json()["items"][0]
    full = listed["items"][0]
    assert "assignments" not in lean and "assignments" in full
    assert lean["params"] == full["params"] and len(lean["steps"]) == len(full["steps"])
    for st in lean["steps"]:
        assert not {"before_state", "output", "evidence", "checklist", "message", "verify_note"} & set(st)
    assert {"before_state", "output", "evidence"} <= set(full["steps"][0])

    # The lifecycle queue (raiseloads evidence/assignments) still sees the job's steps.
    me = (await admin.get("/api/core/me")).json()
    async with get_sessionmaker()() as s:
        j = await s.get(Job, uuid.UUID(full["id"]))
        j.assignee_user_id, j.assignee_name = uuid.UUID(me["id"]), "Admin"  # type: ignore[union-attr]
        await s.commit()
    q = (await admin.get("/api/core/me/next")).json()
    assert q["failed"] == []
    assert f"job:{full['id']}" in [i["key"] for i in q["items"]]
    assert (await admin.get("/api/core/me/summary")).json()["failed"] == []


async def test_requests_closed_in_vietnamese_drop_their_draft_jobs(admin) -> None:  # type: ignore[no-untyped-def]
    """A Vietnamese SDP reports "Đã đóng" / "Đã xử lý": no draft is created for it, and a draft made while the
    request was open is cancelled (with its reason) once the request closes – it leaves the team queue."""
    from app.modules.lifecycle.jobs_api import auto_create_jobs, cancel_closed_jobs
    from app.modules.lifecycle.settings import LifecycleSettings

    s0 = LifecycleSettings()
    assert s0.is_closed("Đã đóng") and s0.is_closed("da xu ly") and s0.is_closed("Closed")
    assert not s0.is_closed("Đang mở") and not s0.is_closed(None)
    old = LifecycleSettings(sdp_closed_statuses=["Closed", "Resolved", "Cancelled", "Canceled"])
    assert old.is_closed("Đã đóng")  # the old English-only default is upgraded

    async with get_sessionmaker()() as s:
        for rid, emp, status in (("9479", "004450", "Đang mở"), ("9478", "004451", "Đã xử lý")):
            s.add(LifecycleRequest(id=rid, display_id=f"7{rid[1:]}", subject="x", employee_id=emp, full_name="Lê E",
                                   kind="offboard", status=status, effective_date=date(2026, 10, 1), fields={},
                                   parse_errors=[], description_text=""))  # fmt: skip
        await s.commit()
        await auto_create_jobs(s)
        await s.commit()
    assert (await admin.get("/api/core/jobs", params={"q": "004451"})).json()[
        "total"
    ] == 0  # closed: no draft
    job = (await admin.get("/api/core/jobs", params={"q": "004450"})).json()["items"][0]
    assert job["status"] == "draft"

    async with get_sessionmaker()() as s:  # HR / IT closed it on SDP; the next sync reads "Đã đóng"
        (await s.get(LifecycleRequest, "9479")).status = "Đã đóng"  # type: ignore[union-attr]
        await s.commit()
        assert await cancel_closed_jobs(s) >= 1
        await s.commit()
        assert await cancel_closed_jobs(s) == 0  # idempotent
    got = (await admin.get(f"/api/core/jobs/{job['id']}")).json()
    assert got["status"] == "cancelled" and "đã đóng trên SDP" in got["close_reason"]


async def test_closed_request_jobs_cancel_and_reopen_with_the_request(admin) -> None:  # type: ignore[no-untyped-def]
    from app.core.workflow.models import JobStep
    from app.modules.lifecycle.jobs_api import (
        cancel_closed_jobs,
        create_job_for_request,
        reopen_reopened_jobs,
    )

    ids = {"9611": "004461", "9612": "004462", "9613": "004463", "9614": "004464"}
    jobs: dict[str, uuid.UUID] = {}
    async with get_sessionmaker()() as s:
        for rid, emp in ids.items():
            req = LifecycleRequest(id=rid, display_id=f"7{rid[1:]}", subject="x", employee_id=emp, full_name="Lê F",
                                   kind="offboard", status="Đang mở", effective_date=date(2026, 10, 1), fields={},
                                   parse_errors=[], description_text="")  # fmt: skip
            s.add(req)
            await s.flush()
            job, _ = await create_job_for_request(s, Actor(None, "t"), req)
            jobs[rid] = job.id
        await s.commit()
        j2 = await s.get(Job, jobs["9612"], populate_existing=True)
        j2.status, j2.current_step = "failed", "check_request"  # type: ignore[union-attr]
        j3 = await s.get(Job, jobs["9613"])
        j3.status = "failed"  # type: ignore[union-attr]
        steps = (
            await s.scalars(select(JobStep).where(JobStep.job_id == jobs["9613"]).order_by(JobStep.seq))
        ).all()
        steps[-1].before_state = {"x": 1}  # a later write step did something
        for rid in ids:
            (await s.get(LifecycleRequest, rid)).status = "Đã đóng"  # type: ignore[union-attr]
        await s.commit()
        assert await cancel_closed_jobs(s) >= 3
        await s.commit()
    async with get_sessionmaker()() as s:
        got = {r: await s.get(Job, j, populate_existing=True) for r, j in jobs.items()}
        assert got["9611"].status == "cancelled" and "auto_cancelled_at" in got["9611"].params  # type: ignore[union-attr]
        assert "Job nháp tự huỷ" in got["9611"].close_reason  # type: ignore[union-attr]
        assert got["9612"].status == "cancelled" and "chưa ghi gì ra hệ thống" in got["9612"].close_reason  # type: ignore[union-attr]
        assert got["9613"].status == "failed"  # type: ignore[union-attr]
        j4 = await s.get(Job, jobs["9614"])  # a person-cancelled job is the same minus the marker
        j4.params = {k: v for k, v in j4.params.items() if k != "auto_cancelled_at"}  # type: ignore[union-attr]
        await s.commit()
        assert await reopen_reopened_jobs(s) == 0  # all requests still closed
        for rid in ("9611", "9612", "9614"):
            (await s.get(LifecycleRequest, rid)).status = "Đang mở"  # type: ignore[union-attr]
        await s.commit()
        assert await reopen_reopened_jobs(s) == 2
        await s.commit()
        assert await reopen_reopened_jobs(s) == 0
    async with get_sessionmaker()() as s:
        for rid, status in (("9611", "draft"), ("9612", "draft"), ("9614", "cancelled")):
            j = await s.get(Job, jobs[rid], populate_existing=True)
            assert j.status == status and "auto_cancelled_at" not in j.params  # type: ignore[union-attr]
        (await s.get(LifecycleRequest, "9614")).status = "Đã đóng"  # type: ignore[union-attr]
        await s.commit()
    r = await admin.post("/api/lifecycle/jobs", json={"request_id": "9614"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "request_closed", r.text


async def test_manual_cancel_after_auto_cancel_is_not_undone_by_sync(admin) -> None:  # type: ignore[no-untyped-def]
    from app.core.workflow import engine
    from app.modules.lifecycle.jobs_api import (
        cancel_closed_jobs,
        create_job_for_request,
        reopen_reopened_jobs,
    )

    person = Actor(None, "supervisor")
    async with get_sessionmaker()() as s:
        req = LifecycleRequest(id="9691", display_id="79691", subject="x", employee_id="004491", full_name="P",
                               kind="offboard", status="Đang mở", effective_date=date(2026, 10, 1), fields={},
                               parse_errors=[], description_text="")  # fmt: skip
        s.add(req)
        await s.flush()
        job, _ = await create_job_for_request(s, Actor(None, "t"), req)
        jid = job.id
        req.status = "Đã đóng"
        await s.commit()
        assert await cancel_closed_jobs(s) == 1
        await s.commit()
        job = await s.get(Job, jid, populate_existing=True)
        await engine.reopen_job(s, person, job, "request mở lại rồi")
        await engine.cancel_job(s, person, job, "trùng, không cần")
        req.status = "Đang mở"
        await s.commit()
        assert await reopen_reopened_jobs(s) == 0
        await s.commit()
        job = await s.get(Job, jid, populate_existing=True)
        assert job.status == "cancelled" and "auto_cancelled_at" not in job.params


async def test_failed_job_auto_cancel_only_for_cancelled_request(admin) -> None:  # type: ignore[no-untyped-def]
    from app.modules.lifecycle.jobs_api import cancel_closed_jobs, create_job_for_request

    jobs: dict[str, uuid.UUID] = {}
    async with get_sessionmaker()() as s:
        for rid, emp in (("9621", "004471"), ("9622", "004472")):
            req = LifecycleRequest(id=rid, display_id=f"7{rid[1:]}", subject="x", employee_id=emp, full_name="P",
                                   kind="offboard", status="Đang mở", effective_date=date(2026, 10, 1), fields={},
                                   parse_errors=[], description_text="")  # fmt: skip
            s.add(req)
            await s.flush()
            job, _ = await create_job_for_request(s, Actor(None, "t"), req)
            job.status, job.current_step = "failed", job.steps[-1].key
            req.status = "Đã xử lý" if rid == "9621" else "Đã huỷ"
            jobs[rid] = job.id
        await s.commit()
        assert await cancel_closed_jobs(s) == 1
        await s.commit()
        assert (await s.get(Job, jobs["9621"], populate_existing=True)).status == "failed"  # type: ignore[union-attr]
        assert (await s.get(Job, jobs["9622"], populate_existing=True)).status == "cancelled"  # type: ignore[union-attr]


async def test_transfer_request_gets_a_manual_group_job(admin) -> None:  # type: ignore[no-untyped-def]
    """RTR "Thay đổi cấp trên trực tiếp" / "Thuyên chuyển": a job with verified manual AD group steps."""
    from app.modules.lifecycle.jobs_api import auto_create_jobs

    async with get_sessionmaker()() as s:
        s.add(
            LifecycleRequest(
                id="9197",
                display_id="7997",
                subject="x",
                employee_id="003516",
                full_name="Nguyễn Lê Cẩm Ly",
                change_type_raw="Thay đổi cấp trên trực tiếp",
                kind="transfer",
                effective_date=date(2026, 11, 1),
                fields={"department": "Nghiên Cứu Cổ Phiếu"},
                parse_errors=[],
                description_text="",
            )
        )
        await s.commit()
        assert await auto_create_jobs(s) >= 1
        await s.commit()
    [item] = (await admin.get("/api/core/jobs", params={"q": "003516"})).json()["items"]
    j = (await admin.get(f"/api/core/jobs/{item['id']}")).json()
    assert j["workflow_key"] == "lifecycle.transfer" and j["status"] == "draft"
    steps = {x["key"]: x for x in j["steps"]}
    assert list(steps) == ["check_request", "find_sdp_user", "groups_remove", "groups_add", "close_request"]
    assert steps["groups_remove"]["kind"] == steps["groups_add"]["kind"] == "manual"
    # Operators confirm both group steps with evidence.
    from app.modules.lifecycle.workflows import TRANSFER

    assert all(x.evidence_required for x in TRANSFER.steps if x.key.startswith("groups_"))


async def test_checklist_versions_and_snapshot(admin, client) -> None:  # type: ignore[no-untyped-def]
    flows = {w["key"]: w for w in (await admin.get("/api/core/workflows?module=lifecycle")).json()}
    # Processes with operator checklists (the reassignment workflow has none and is not drawn).
    assert {k for k, w in flows.items() if w["verify_steps"]} == {
        "lifecycle.onboard",
        "lifecycle.offboard",
        "lifecycle.mailbox_release",
        "lifecycle.transfer",
    }
    onboard = flows["lifecycle.onboard"]
    assert onboard["verify_steps"] and onboard["checklist_version"] == 0
    steps = {s["key"]: s["checklist"] for s in onboard["steps"]}
    assert steps["assign_assets"]["evidence_required"] and steps["assign_assets"]["items"]

    # A job started on version 0 keeps it after the checklist changes.
    async with get_sessionmaker()() as s:
        s.add(
            LifecycleRequest(
                id="9188",
                display_id="7988",
                subject="x",
                employee_id="004488",
                full_name="Nhân Viên Thử",
                kind="onboard",
                effective_date=date(2026, 11, 30),
                fields={},
                parse_errors=[],
                description_text="",
            )
        )
        await s.commit()
    j = (await admin.post("/api/lifecycle/jobs", json={"request_id": "9188"})).json()
    j = (await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})).json()
    assert j["checklist_version"] == 0

    steps["check_request"] = {
        "items": [{"id": "k1", "text": "Đã gọi xác nhận với HR"}],
        "evidence_required": True,
    }
    await client.login("op-cl@test.local")  # no role → no permission
    assert (
        await client.put("/api/core/workflows/lifecycle.onboard/checklist", json={"steps": steps})
    ).status_code == 403
    bad = await admin.put(
        "/api/core/workflows/lifecycle.onboard/checklist", json={"steps": {**steps, "nope": {"items": []}}}
    )
    assert bad.json()["error"]["code"] == "unknown_step"
    saved = await admin.put(
        "/api/core/workflows/lifecycle.onboard/checklist", json={"steps": steps, "note": "Thêm gọi HR"}
    )
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["checklist_version"] == 1 and out["history"][0]["note"] == "Thêm gọi HR"
    same = await admin.put("/api/core/workflows/lifecycle.onboard/checklist", json={"steps": steps})
    assert same.json()["error"]["code"] == "no_change"

    running = (await admin.get(f"/api/core/jobs/{j['id']}")).json()
    assert (
        step(running, "check_request")["checklist"][0]["text"] == "Request đúng nhân viên và loại biến động"
    )
    assert not step(running, "check_request")["evidence_required"]
    await admin.post(f"/api/core/jobs/{j['id']}/cancel", json={"reason": "dọn dữ liệu thử"})
    async with get_sessionmaker()() as s:
        actions = (
            await s.scalars(select(AuditLog.action).where(AuditLog.target_id == "lifecycle.onboard"))
        ).all()
    assert "workflow.checklist_saved" in actions


@respx.mock(assert_all_called=False)
async def test_job_assignee_follows_sdp_and_reassign_writes_back(admin, client, systems) -> None:  # type: ignore[no-untyped-def]
    from app.modules.lifecycle.assignment import sync_assignees

    roles = {r["code"]: r["id"] for r in (await admin.get("/api/core/roles")).json()}
    for upn in ("op-assign1@test.local", "op-assign2@test.local"):
        await admin.post(
            "/api/core/users", json={"upn": upn, "display_name": upn, "role_ids": [roles["operator"]]}
        )
    await admin.post(
        "/api/core/users",
        json={"upn": "sup-assign@test.local", "display_name": "Sup", "role_ids": [roles["supervisor"]]},
    )
    async with get_sessionmaker()() as s:
        req = await s.get(LifecycleRequest, "9102")
        req.technician_id, req.technician_name, req.technician_email = (
            "70",
            "Tech 70",
            "op-assign1@test.local",
        )
        old = await s.scalar(select(Job).where(Job.idempotency_key == "lifecycle:offboard:9102"))
        if old is not None:
            await s.delete(old)
        await s.commit()
    j = (await admin.post("/api/lifecycle/jobs", json={"request_id": "9102"})).json()
    assert j["assignee"]["name"] == "Tech 70" and len(j["assignments"]) == 1

    await client.login("op-assign1@test.local")
    mine = (
        await client.get("/api/core/jobs?assignee=me&workflows=lifecycle.onboard,lifecycle.offboard")
    ).json()
    assert [x["id"] for x in mine["items"]] == [j["id"]]
    summary = {i["key"]: i["count"] for i in (await client.get("/api/core/me/summary")).json()["items"]}
    assert summary["jobs:open"] == 1 and summary["jobs:done"] == 0
    # A job completed this week counts as done (closed_at).
    async with get_sessionmaker()() as s:
        jb = await s.get(Job, j["id"])
        prev = jb.status
        jb.status, jb.closed_at = "completed", datetime.now(UTC)
        await s.commit()
        summary = {i["key"]: i["count"] for i in (await client.get("/api/core/me/summary")).json()["items"]}
        assert summary["jobs:open"] == 0 and summary["jobs:done"] == 1
        jb.status, jb.closed_at = prev, None
        await s.commit()
    assert (
        await client.post(f"/api/lifecycle/jobs/{j['id']}/assign", json={"user_id": j["id"]})
    ).status_code == 403

    # Reassigned on SDP → the job follows at the next sync.
    async with get_sessionmaker()() as s:
        req = await s.get(LifecycleRequest, "9102")
        req.technician_name, req.technician_email = "Tech 72", "nobody@test.local"
        await s.commit()
        assert await sync_assignees(s) >= 1
        await s.commit()
    j = (await admin.get(f"/api/core/jobs/{j['id']}")).json()
    assert j["assignee"]["name"] == "Tech 72" and j["assignments"][-1]["source"] == "sdp"

    # A supervisor hands it to op-assign2: SDP is written by the worker (with the supervisor's own key).
    await client.login("sup-assign@test.local")
    sdp_id = {p["name"]: p["id"] for p in (await admin.get("/api/core/connections")).json()}["SDP WF"]
    r = await client.get("/api/core/me/credentials")
    assert [c["credential"] for c in r.json() if c["profile_id"] == sdp_id] == [None]  # not entered yet
    early = {p["upn"]: p["id"] for p in (await client.get("/api/lifecycle/assignees")).json()}
    r = await client.post(  # no personal key yet: refused up front, nothing left waiting
        f"/api/lifecycle/jobs/{j['id']}/assign", json={"user_id": early["op-assign2@test.local"]}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "personal_credential_missing", r.text
    r = await client.put(
        f"/api/core/me/credentials/{sdp_id}",
        json={"auth_type": "api_key", "secrets": {"api_key": "sup-0000000001"}},
    )
    assert r.status_code == 200 and "sup-0000000001" not in r.text
    people = {p["upn"]: p["id"] for p in (await client.get("/api/lifecycle/assignees")).json()}
    assert "op-assign2@test.local" in people and "sup-assign@test.local" in people
    r = await client.post(
        f"/api/lifecycle/jobs/{j['id']}/assign",
        json={"user_id": people["op-assign2@test.local"], "reason": "Nghỉ phép"},
    )
    assert r.status_code == 201, r.text
    from app.core.scheduler.service import get_task
    from app.core.workflow.engine import PUMP_TASK_KEY

    async with get_sessionmaker()() as s:  # the pump was requested in the same commit as the assignment
        assert (await get_task(s, PUMP_TASK_KEY)).requested_by == "sup-assign@test.local"
        aj = await s.get(Job, r.json()["id"])
        aj.status = "waiting"  # what the engine stores on PersonalCredentialMissing
        await s.commit()
    st = (await client.get(f"/api/lifecycle/jobs/{j['id']}/assignment")).json()
    assert st["pending"] is True and st["status"] == "waiting" and st["waiting_reason"] == "personal_key"
    assert st["mine"] is False and st["requested_by_name"] == "Sup"
    async with get_sessionmaker()() as s:
        aj = await s.get(Job, r.json()["id"])
        aj.status = "running"
        await s.commit()
    await run_worker()
    assign_job = (await admin.get(f"/api/core/jobs/{r.json()['id']}")).json()
    assert assign_job["status"] == "completed", assign_job
    assert "technician:9102:71" in systems.calls
    j = (await admin.get(f"/api/core/jobs/{j['id']}")).json()
    last = j["assignments"][-1]
    assert j["assignee"]["name"] == "Tech 71" and last["source"] == "app" and last["reason"] == "Nghỉ phép"
    assert last["from_name"] == "Tech 72"
    # The reassignment job never shows in the onboard/offboard list.
    listed = (
        await admin.get("/api/core/jobs?workflows=lifecycle.onboard,lifecycle.offboard&limit=500")
    ).json()
    assert all(x["workflow_key"] != "lifecycle.assign" for x in listed["items"])

    # "Nhận Job": an operator may take only an unassigned job (same SDP write-back path, to themselves).
    await client.login("op-assign2@test.local")
    r = await client.post(f"/api/lifecycle/jobs/{j['id']}/claim")
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_assigned"
    async with get_sessionmaker()() as s:
        jb = await s.get(Job, j["id"])
        jb.assignee_user_id, jb.assignee_name = None, None
        await s.commit()
    # Without their own SDP key the claim is refused up front: no assignment job is left waiting.
    n_before = len((await admin.get("/api/core/jobs?workflows=lifecycle.assign&limit=500")).json()["items"])
    r = await client.post(f"/api/lifecycle/jobs/{j['id']}/claim")
    assert r.status_code == 409 and r.json()["error"]["code"] == "personal_credential_missing", r.text
    assert (
        len((await admin.get("/api/core/jobs?workflows=lifecycle.assign&limit=500")).json()["items"])
        == n_before
    )
    assert (await client.get(f"/api/lifecycle/jobs/{j['id']}/assignment")).json() == {"pending": False}
    assert (
        await client.put(
            f"/api/core/me/credentials/{sdp_id}",
            json={"auth_type": "api_key", "secrets": {"api_key": "op2-0000000001"}},
        )
    ).status_code == 200
    r = await client.post(f"/api/lifecycle/jobs/{j['id']}/claim")
    assert r.status_code == 201, r.text
    # The pump was requested (runs within seconds) and the pending assignment is readable by the operator.
    async with get_sessionmaker()() as s:
        assert (await get_task(s, PUMP_TASK_KEY)).requested_by == "op-assign2@test.local"
    st = (await client.get(f"/api/lifecycle/jobs/{j['id']}/assignment")).json()
    assert st["pending"] is True and st["to_name"] == "op-assign2@test.local"
    assert st["mine"] is True and st["waiting_reason"] is None
    await client.login("op-assign1@test.local")
    other = (await client.get(f"/api/lifecycle/jobs/{j['id']}/assignment")).json()  # unassigned: anyone sees
    assert other["mine"] is False and {**other, "mine": True} == st
    await client.login("op-assign2@test.local")
    claim = (await admin.get(f"/api/core/jobs/{r.json()['id']}")).json()
    assert claim["workflow_key"] == "lifecycle.assign" and claim["params"]["reason"] == "Nhận Job"
    # A second person pressing "Nhận" before the SDP write-back finished is refused.
    await client.login("op-assign1@test.local")
    r = await client.post(f"/api/lifecycle/jobs/{j['id']}/claim")
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_being_assigned", r.text
    # Once the job belongs to someone else, another operator may not read its assignment.
    from app.core.auth.models import User

    async with get_sessionmaker()() as s:
        jb = await s.get(Job, j["id"])
        other = await s.scalar(select(User).where(User.upn == "op-assign2@test.local"))
        jb.assignee_user_id, jb.assignee_name = other.id, "op2"
        await s.commit()
    assert (await client.get(f"/api/lifecycle/jobs/{j['id']}/assignment")).status_code == 403


async def test_pump_is_not_starved_by_jobs_waiting_on_people(admin) -> None:  # type: ignore[no-untyped-def]
    from app.core.workflow.models import JobStep

    def make(n: int, status: str, step_status: str, updated_at: datetime | None) -> Job:
        return Job(
            workflow_key="lifecycle.onboard",
            module_id="lifecycle",
            status=status,
            subject_key=f"starve-{n}",
            subject_label="x",
            idempotency_key=f"test:starve:{n}",
            params={},
            created_by="test",
            steps=[JobStep(seq=1, key="biostar_enroll", title="BioStar", kind="manual", status=step_status)],
            evidence=[],
            assignments=[],
            **({"updated_at": updated_at} if updated_at else {}),
        )

    old = datetime(2020, 1, 1, tzinfo=UTC)
    async with get_sessionmaker()() as s:
        jobs = [make(n, "waiting", "waiting", old) for n in range(21)]
        fresh = make(99, "running", "pending", None)
        s.add_all([*jobs, fresh])
        await s.commit()
        ids = [j.id for j in [*jobs, fresh]]
    try:
        await run_worker()
        j = await job(admin, str(ids[-1]))
        assert j["status"] == "waiting" and step(j, "biostar_enroll")["status"] == "waiting"
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(Job.__table__.delete().where(Job.id.in_(ids)))
            await s.commit()


async def test_step_waits_for_the_responsible_persons_own_sdp_key(admin, systems) -> None:  # type: ignore[no-untyped-def]
    """No personal key: the SDP step waits (not fails) with how to fix it; with the key it runs as that person."""
    from app.core.workflow.models import JobStep

    sdp_id = {p["name"]: p["id"] for p in (await admin.get("/api/core/connections")).json()}["SDP WF"]
    assert (await admin.delete(f"/api/core/me/credentials/{sdp_id}")).status_code == 204
    async with get_sessionmaker()() as s:  # a fresh job for 9101 (other tests may have run it)
        from sqlalchemy import delete

        from app.core.workflow.models import Job

        await s.execute(delete(Job).where(Job.external_ref == "9101"))
        await s.commit()
    systems.calls.clear()
    j = (await admin.post("/api/lifecycle/jobs", json={"request_id": "9101"})).json()
    j = (await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})).json()
    await run_worker()
    j = await job(admin, j["id"])
    first = step(j, "check_request")
    assert j["status"] == "waiting" and first["status"] == "waiting", j
    assert "chưa nhập API key cá nhân" in first["message"] and "Khoá API của tôi" in first["message"]
    assert systems.calls == []  # nothing was sent with the shared key

    mine = await admin.put(
        f"/api/core/me/credentials/{sdp_id}",
        json={"auth_type": "api_key", "secrets": {"api_key": "admin-key-0001"}},
    )
    assert mine.status_code == 200
    async with get_sessionmaker()() as s:  # do not wait the 2 minutes of the next check
        st = await s.scalar(select(JobStep).where(JobStep.job_id == j["id"], JobStep.key == "check_request"))
        st.next_run_at = None
        await s.commit()
    await run_worker()
    assert step(await job(admin, j["id"]), "check_request")["status"] == "verify"


async def test_prepare_collect_waits_for_personal_key_but_falls_back_on_network_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from app.core.errors import PersonalCredentialMissing
    from app.modules.lifecycle import workflows as wf

    c = SimpleNamespace(params={"sdp_user_id": "9", "employee": {"id": "003551"}}, outputs={}, session=None)

    async def snap(_s, _emp):  # type: ignore[no-untyped-def]
        return [{"id": "1", "name": "LT-1"}]

    monkeypatch.setattr(wf, "_snapshot_assets", snap)

    async def no_key(*_a):  # type: ignore[no-untyped-def]
        raise PersonalCredentialMissing("no key")

    monkeypatch.setattr(wf, "_sdp", no_key)
    with pytest.raises(PersonalCredentialMissing):
        await wf.prepare_collect(c)  # type: ignore[arg-type]

    async def down(*_a):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("down")

    monkeypatch.setattr(wf, "_sdp", down)
    out = await wf.prepare_collect(c)  # type: ignore[arg-type]
    assert out.output["source"] == "lần đồng bộ gần nhất" and out.output["assets"][0]["id"] == "1"
