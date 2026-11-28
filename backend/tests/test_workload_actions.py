"""Quick ticket actions (note / close as Resolved): worker Jobs run with the person's own SDP key."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from sqlalchemy import delete, or_, select, update

from app.core.auth.models import User
from app.core.connections.models import UserCredential
from app.core.db import get_sessionmaker
from app.core.workflow.engine import create_job, pump
from app.core.workflow.models import Job
from app.modules.workload.models import Ticket
from tests.test_workload import OK, SDP, FakeSdp, _user

REQ = re.escape(SDP) + r"/api/v3/requests/(?P<rid>\d+)"


class ActionSdp(FakeSdp):
    def __init__(self) -> None:
        super().__init__()
        self.notes: dict[str, list[dict]] = {}  # type: ignore[type-arg]
        self.note_posts: list[tuple[str, str, str]] = []  # (request, authtoken, html)
        self.status_puts: list[tuple[str, str, str]] = []  # (request, authtoken, status)

    def install(self, r: respx.MockRouter) -> None:
        super().install(r)  # the same pattern below replaces the parent's single-request route

        def notes(request: httpx.Request, rid: str) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(parse_qs(request.content.decode())["input_data"][0])["note"]
                self.notes.setdefault(rid, []).append({"description": body["description"]})
                self.note_posts.append((rid, request.headers["authtoken"], body["description"]))
                return httpx.Response(201, json={**OK, "note": {"id": "1"}})
            rows = self.notes.get(rid, [])
            return httpx.Response(200, json={**OK, "list_info": {"has_more_rows": False}, "notes": rows})

        r.route(url__regex=rf"{REQ}/notes(\?.*)?$").mock(side_effect=notes)

        def status(request: httpx.Request, rid: str) -> httpx.Response:
            if request.method == "PUT":
                name = json.loads(parse_qs(request.content.decode())["input_data"][0])["request"]["status"]
                self.status_puts.append((rid, request.headers["authtoken"], name["name"]))
                self.requests[rid]["status"] = name
                return httpx.Response(200, json={**OK, "request": self.requests[rid]})
            return httpx.Response(200, json={**OK, "request": self.requests[rid]})

        r.route(url__regex=rf"{REQ}$").mock(side_effect=status)


@pytest.fixture
async def sdp(admin, respx_mock):  # type: ignore[no-untyped-def]
    fake = ActionSdp()
    fake.install(respx_mock)
    prof = next((p for p in (await admin.get("/api/core/connections")).json() if p["name"] == "SDP WL"), None)
    if prof is None:
        prof = (
            await admin.post(
                "/api/core/connections",
                json={"system_type": "sdp", "name": "SDP WL", "base_url": SDP, "config": {}},
            )
        ).json()
    for purpose in ("read", "write"):
        await admin.put(
            f"/api/core/connections/{prof['id']}/credentials/{purpose}",
            json={"auth_type": "api_key", "secrets": {"api_key": f"shared-{purpose}-0000"}},
        )
    values = (await admin.get("/api/workload/settings")).json()["values"]
    assert (
        await admin.put("/api/workload/settings", json={**values, "sdp_profile_id": prof["id"]})
    ).status_code == 200
    for role, upn in (
        ("manager", "wl-mgr@test.local"),
        ("operator", "wl-op1@test.local"),
        ("operator", "wl-op2@test.local"),
        ("viewer", "wl-view@test.local"),
    ):
        await _user(admin, role, upn)
    from app.modules.workload.sync import run_sync

    async with get_sessionmaker()() as s:
        await run_sync(s)
    from tests.personal_keys import give_personal_keys

    await give_personal_keys(prof["id"])
    return fake


async def _run() -> None:
    async with get_sessionmaker()() as s:
        await pump(s)


async def _reset_ticket(tid: str, status: str = "Open", state: str = "open") -> None:
    async with get_sessionmaker()() as s:
        # Jobs left open by an earlier test would block this ticket (C-16/C-17).
        await s.execute(
            update(Job)
            .where(
                Job.status.notin_(("completed", "cancelled")),
                or_(Job.external_ref == tid, Job.params["request_id"].astext == tid),
            )
            .values(status="cancelled")
        )
        t = await s.get(Ticket, tid)
        assert t is not None
        t.status, t.state, t.phase, t.completed_at = (
            status,
            state,
            "pending" if state == "open" else None,
            None,
        )
        await s.commit()


@respx.mock(assert_all_called=False)
async def test_note_is_escaped_idempotent_and_uses_personal_key(admin, client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    await client.login("wl-op1@test.local")
    resp = await client.post("/api/workload/tickets/501/note", json={"text": "<script>x</script>\nxong"})
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    await _run()
    job = (await client.get(f"/api/core/jobs/{job_id}")).json()
    assert job["status"] == "completed", job
    assert len(sdp.note_posts) == 1
    _, token, html = sdp.note_posts[0]
    assert token == "key-wl-op1@test.local"
    assert "&lt;script&gt;" in html and "<script>" not in html and "<br>" in html
    assert f"(infra-ops #{job_id[:8]})" in html
    assert job["steps"][0]["before_state"] == {"notes": 0}

    # Retry after the note already reached SDP (suffix present): no second post.
    async with get_sessionmaker()() as s:
        j = await s.get(Job, __import__("uuid").UUID(job_id))
        assert j is not None
        j.steps[0].status, j.status = "pending", "running"
        await s.commit()
    await _run()
    assert len(sdp.note_posts) == 1
    audit = (await admin.get("/api/core/audit?action=workload.ticket_note_requested")).json()
    assert audit["items"]


@respx.mock(assert_all_called=False)
async def test_close_confirms_reads_first_and_updates_local_ticket(admin, client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    sdp.requests["501"]["status"] = {"name": "Open"}
    await client.login("wl-op1@test.local")
    short = await client.post("/api/workload/tickets/501/close", json={"result": "ok"})
    assert short.status_code == 422
    resp = await client.post("/api/workload/tickets/501/close", json={"result": "Đã thay chuột cho khách."})
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    await _run()
    assert len(sdp.note_posts) == 1 and not sdp.status_puts  # waits for the confirmation first
    assert (
        await client.post(f"/api/core/jobs/{job_id}/steps/status/confirm", json={"typed": "D501"})
    ).status_code == 200
    await _run()
    job = (await client.get(f"/api/core/jobs/{job_id}")).json()
    assert job["status"] == "completed", job
    assert sdp.status_puts == [("501", "key-wl-op1@test.local", "Resolved")]
    assert job["steps"][1]["before_state"] == {"status": "Open"}
    async with get_sessionmaker()() as s:
        t = await s.get(Ticket, "501")
        assert t is not None and t.status == "Resolved" and t.state == "done" and t.completed_at is not None
    assert (await admin.get("/api/core/audit?action=workload.ticket_close_requested")).json()["items"]

    # Already Resolved on SDP: a new close job reads first and does not write again.
    await _reset_ticket("501")
    resp = await client.post("/api/workload/tickets/501/close", json={"result": "Đóng lần hai."})
    await _run()
    await client.post(f"/api/core/jobs/{resp.json()['id']}/steps/status/confirm", json={"typed": "D501"})
    await _run()
    assert len(sdp.status_puts) == 1
    assert (await client.get(f"/api/core/jobs/{resp.json()['id']}")).json()["status"] == "completed"


@respx.mock(assert_all_called=False)
async def test_scope_and_permission(admin, client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    await client.login("wl-op2@test.local")  # 501 belongs to technician 11
    assert (await client.post("/api/workload/tickets/501/note", json={"text": "x"})).status_code == 404
    assert (
        await client.post("/api/workload/tickets/501/close", json={"result": "xxxxxx"})
    ).status_code == 404
    await client.login("wl-view@test.local")
    assert (await client.post("/api/workload/tickets/501/note", json={"text": "x"})).status_code == 403
    await client.login("wl-mgr@test.local")  # coordinators act on any open ticket
    assert (await client.post("/api/workload/tickets/501/note", json={"text": "x"})).status_code == 201
    assert (await client.post("/api/workload/tickets/502/note", json={"text": "x"})).json()["error"][
        "code"
    ] == "ticket_not_open"
    assert not sdp.note_posts  # nothing reached SDP from the API calls
    await _run()  # leave no pending job for the next test
    assert [p[1] for p in sdp.note_posts] == ["key-wl-mgr@test.local"]


@respx.mock(assert_all_called=False)
async def test_close_refused_while_another_job_references_the_ticket(client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    async with get_sessionmaker()() as s:
        job, _ = await create_job(
            s,
            (await _actor(s)),
            "workload.reassign",
            params={
                "request_id": "501",
                "ticket_ids": ["501"],
                "to_technician_id": "12",
                "to_technician_name": "x",
            },
            subject_key="x",
            subject_label="Job khác",
            external_ref=None,
        )
        await s.commit()
        other_id = str(job.id)
    await client.login("wl-op1@test.local")
    resp = await client.post("/api/workload/tickets/501/close", json={"result": "Xong rồi nhé."})
    err = resp.json()["error"]
    assert resp.status_code == 400 and err["code"] == "ticket_has_open_job"
    assert err["details"]["job_id"] == other_id


async def _actor(s):  # type: ignore[no-untyped-def]
    from app.core.audit.service import Actor

    u = await s.scalar(select(User).where(User.upn == "wl-mgr@test.local"))
    return Actor(user_id=u.id, upn=u.upn, ip=None)


@respx.mock(assert_all_called=False)
async def test_missing_personal_key_waits_without_shared_key(client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    async with get_sessionmaker()() as s:
        u = await s.scalar(select(User).where(User.upn == "wl-op1@test.local"))
        await s.execute(delete(UserCredential).where(UserCredential.user_id == u.id))
        await s.commit()
    await client.login("wl-op1@test.local")
    resp = await client.post("/api/workload/tickets/501/note", json={"text": "chờ khoá"})
    assert resp.status_code == 201, resp.text
    await _run()
    job = (await client.get(f"/api/core/jobs/{resp.json()['id']}")).json()
    assert job["status"] == "waiting" and job["steps"][0]["status"] == "waiting", job
    assert not sdp.note_posts


async def _other_job(status: str = "failed") -> None:
    """A lifecycle-like Job (not the close Job) that references request 501 through external_ref."""
    async with get_sessionmaker()() as s:
        job, _ = await create_job(
            s,
            (await _actor(s)),
            "workload.reassign",
            params={"ticket_ids": ["501"], "to_technician_id": "12", "to_technician_name": "x"},
            subject_key="x",
            subject_label="Job khác",
            external_ref="501",
        )
        job.status = status
        await s.commit()


async def _close_job(client) -> str:  # type: ignore[no-untyped-def]
    resp = await client.post("/api/workload/tickets/501/close", json={"result": "Đã xử lý xong."})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _confirm_and_run(client, job_id: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    await _run()
    await client.post(f"/api/core/jobs/{job_id}/steps/status/confirm", json={"typed": "D501"})
    await _run()
    return (await client.get(f"/api/core/jobs/{job_id}")).json()  # type: ignore[no-any-return]


@respx.mock(assert_all_called=False)
async def test_close_does_not_reopen_a_request_already_closed_on_sdp(client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    await client.login("wl-op1@test.local")
    job_id = await _close_job(client)
    sdp.requests["501"]["status"] = {"name": "Closed"}  # closed on SDP meanwhile
    job = await _confirm_and_run(client, job_id)
    assert job["status"] == "completed" and "Closed" in job["steps"][1]["message"], job
    assert not sdp.status_puts
    async with get_sessionmaker()() as s:
        t = await s.get(Ticket, "501")
        assert t is not None and t.status == "Closed" and t.state == "done"


@respx.mock(assert_all_called=False)
async def test_failed_job_with_external_ref_blocks_close_api_and_run_time(client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    await _other_job("failed")
    await client.login("wl-op1@test.local")
    resp = await client.post("/api/workload/tickets/501/close", json={"result": "Đã xử lý xong."})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "ticket_has_open_job"
    await _reset_ticket("501")  # cancels the other job

    job_id = await _close_job(client)
    await _run()
    await _other_job("failed")  # appears after the close Job was created
    job = await _confirm_and_run(client, job_id)
    assert job["steps"][1]["status"] == "failed", job
    assert "Job chưa xong" in job["steps"][1]["message"]
    assert not sdp.status_puts


@respx.mock(assert_all_called=False)
async def test_close_fails_when_request_was_reassigned_on_sdp(client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    await client.login("wl-op1@test.local")
    job_id = await _close_job(client)
    sdp.requests["501"]["technician"] = {"id": "12"}
    job = await _confirm_and_run(client, job_id)
    assert job["steps"][1]["status"] == "failed", job
    assert job["steps"][1]["message"] == "Request đã được chuyển cho người khác."
    assert not sdp.status_puts


@respx.mock(assert_all_called=False)
async def test_double_post_close_second_is_refused(client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    await client.login("wl-op1@test.local")
    await _close_job(client)
    again = await client.post("/api/workload/tickets/501/close", json={"result": "Đã xử lý xong."})
    assert again.status_code == 400 and again.json()["error"]["code"] == "ticket_has_open_job"


@respx.mock(assert_all_called=False)
async def test_other_operator_cannot_see_the_job(client, sdp) -> None:  # type: ignore[no-untyped-def]
    await _reset_ticket("501")
    await client.login("wl-op1@test.local")
    job_id = await _close_job(client)
    assert (await client.get(f"/api/core/jobs/{job_id}")).status_code == 200
    await client.login("wl-op2@test.local")
    listed = (await client.get("/api/core/jobs")).json()
    assert job_id not in [j["id"] for j in listed["items"]]
    assert (await client.get(f"/api/core/jobs/{job_id}")).status_code in (403, 404)
