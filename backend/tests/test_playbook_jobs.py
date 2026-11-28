"""Playbook inside a Job: resolve, scope, ticks, choices, pinning, frozen decisions, delete guard, next action."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update

from app.core.audit.models import AuditLog
from app.core.audit.service import Actor
from app.core.auth.models import User
from app.core.db import get_sessionmaker
from app.core.modules.manifest import FactSource, FactValue
from app.core.modules.registry import get_registry
from app.core.settings.models import ModuleSetting
from app.core.workflow import engine
from app.core.workflow.models import Job
from app.modules.playbook.models import Guide, JobMark, JobRun
from tests.test_workload import _user

SYS = Actor(user_id=None, upn="system", ip=None)


@pytest.fixture(autouse=True)
async def clean() -> None:
    async with get_sessionmaker()() as s:
        await s.execute(delete(Guide))  # cascades to runs and marks
        await s.execute(delete(ModuleSetting).where(ModuleSetting.module_id == "playbook"))
        await s.commit()


class Facts:
    value: str | None = "1"
    seen: list[str]


@pytest.fixture
def facts(monkeypatch: pytest.MonkeyPatch) -> type[Facts]:
    Facts.value, Facts.seen = "1", []

    async def load(session: Any, emp: str, param: str) -> FactValue | None:
        Facts.seen.append(emp)
        return None if Facts.value is None else FactValue(value=Facts.value, source="test")

    src = {"lifecycle.assets_in_use": FactSource("lifecycle.assets_in_use", "Số tài sản", "number", load)}
    monkeypatch.setattr(get_registry(), "facts", lambda: src)
    return Facts


async def guide(admin: Any, template: str, title: str | None = None) -> str:
    r = await admin.post("/api/playbook/guides", json={"title": title or f"{template} {uuid.uuid4().hex[:4]}",
                                                       "template": template})  # fmt: skip
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    p = await admin.post(f"/api/playbook/guides/{gid}/publish", json={"note": "v"})
    assert p.status_code == 201, p.text
    return str(gid)


async def mkjob(
    workflow: str = "lifecycle.offboard", key: str = "003551", assignee: str | None = None
) -> str:
    async with get_sessionmaker()() as s:
        job, _ = await engine.create_job(s, SYS, workflow, params={"employee_id": key}, subject_key=key,
                                         subject_label=f"NV {key}", external_ref=None)  # fmt: skip
        if assignee:
            u = await s.scalar(select(User).where(User.upn == assignee))
            assert u
            await engine.set_assignee(s, job, user_id=u.id, name=u.display_name, actor="t", source="manual")
        await s.commit()
        return str(job.id)


async def setjob(job_id: str, **fields: Any) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == uuid.UUID(job_id)).values(**fields))
        await s.commit()


async def get(c: Any, jid: str) -> dict[str, Any]:
    r = await c.get(f"/api/playbook/jobs/{jid}")
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def ref(p: dict[str, Any]) -> dict[str, Any]:
    return {"guide_id": p["guide"]["id"], "version": p["guide"]["version"]}


async def choose(c: Any, jid: str, p: dict[str, Any], node: str, index: int) -> Any:
    return await c.put(f"/api/playbook/jobs/{jid}/choices/{node}", json=ref(p) | {"index": index})


async def mark(c: Any, jid: str, p: dict[str, Any], node: str, status: str = "done", reason: str = "") -> Any:
    return await c.put(
        f"/api/playbook/jobs/{jid}/marks/{node}", json=ref(p) | {"status": status, "reason": reason}
    )


def trace_ids(p: dict[str, Any]) -> list[str]:
    return [e["id"] for e in p["run"]["trace"]]


async def audits(action: str, jid: str) -> int:
    async with get_sessionmaker()() as s:
        return int(await s.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == action, AuditLog.target_id == jid)) or 0)  # fmt: skip


async def test_resolve_assigned_auto_ambiguous_none(admin, client) -> None:  # type: ignore[no-untyped-def]
    await _user(admin, "operator", "pj-op@test.local")
    jid = await mkjob(assignee="pj-op@test.local")
    assert (await get(admin, jid)) == {"guide": None}  # none: 200, nothing to show
    a = await guide(admin, "offboard", "A")
    p = await get(admin, jid)
    assert p["guide"]["id"] == a and p["pinned"] is False and p["can_edit"] is True
    b = await guide(admin, "offboard", "B")
    p = await get(admin, jid)
    assert p["guide"] is None and {x["id"] for x in p["ambiguous"]} == {a, b}  # candidates for publishers
    await client.login("pj-op@test.local")
    assert await get(client, jid) == {"guide": None}  # an Operator is not told who to ask
    assert (
        await client.put("/api/playbook/settings/job-guides", json={"workflow_guides": {}})
    ).status_code == 403
    # assigned: wins over the guess; invalid assignments are refused
    bad = await admin.put("/api/playbook/settings/job-guides", json={"workflow_guides": {"nope": a}})
    assert bad.json()["error"]["code"] == "unknown_workflow"
    draft = (await admin.post("/api/playbook/guides", json={"title": "Chưa xuất bản"})).json()["id"]
    bad = await admin.put("/api/playbook/settings/job-guides",
                          json={"workflow_guides": {"lifecycle.offboard": draft}})  # fmt: skip
    assert bad.json()["error"]["code"] == "guide_not_published"
    ok = await admin.put(
        "/api/playbook/settings/job-guides", json={"workflow_guides": {"lifecycle.offboard": b}}
    )
    assert ok.status_code == 200, ok.text
    assert (await get(admin, jid))["guide"]["id"] == b
    assert (await get(client, jid))["guide"]["id"] == b
    assert (await admin.get("/api/playbook/settings/job-guides")).json()["workflow_guides"] == {
        "lifecycle.offboard": b
    }
    # the assigned guide was deleted: ignored, falls back to the only published guide left
    assert (await admin.delete(f"/api/playbook/guides/{b}")).status_code == 204
    assert (await get(admin, jid))["guide"]["id"] == a
    # step_guide prefers the assigned guide
    c = await guide(admin, "offboard", "0 first by title")
    q = {"workflow": "lifecycle.offboard", "step": "account_disable"}
    assert (await admin.get("/api/playbook/step-guide", params=q)).json()["guide_id"] == c
    await admin.put("/api/playbook/settings/job-guides", json={"workflow_guides": {"lifecycle.offboard": a}})
    assert (await admin.get("/api/playbook/step-guide", params=q)).json()["guide_id"] == a


async def test_scope_and_permissions(admin, client) -> None:  # type: ignore[no-untyped-def]
    await guide(admin, "offboard")
    await _user(admin, "operator", "pj-o1@test.local")
    await _user(admin, "operator", "pj-o2@test.local")
    perms = ["lifecycle.job.view", "lifecycle.job.view_all", "playbook.guides.view"]
    r = await admin.post("/api/core/roles", json={"code": "pj_reader", "name": "r", "permissions": perms})
    assert r.status_code == 201, r.text  # own role: default viewer grants are changed by other tests
    await _user(admin, "pj_reader", "pj-v@test.local")
    mine = await mkjob(assignee="pj-o1@test.local")
    p = await get(admin, mine)
    await client.login("pj-o2@test.local")
    assert (await client.get(f"/api/playbook/jobs/{mine}")).status_code == 403
    assert (await choose(client, mine, p, "complete", 0)).status_code == 403
    await client.login("pj-v@test.local")  # sees every Job, may not change any
    assert (await get(client, mine))["can_edit"] is False
    assert (await choose(client, mine, p, "complete", 0)).status_code == 403
    r = await admin.post(
        "/api/core/roles", json={"code": "pj_nopb", "name": "x", "permissions": ["lifecycle.job.view"]}
    )
    assert r.status_code == 201, r.text
    roles = {x["code"]: x["id"] for x in (await admin.get("/api/core/roles")).json()}
    await admin.post("/api/core/users", json={"upn": "pj-n@test.local", "display_name": "n",
                                              "role_ids": [roles["pj_nopb"]]})  # fmt: skip
    await client.login("pj-n@test.local")
    assert (await client.get(f"/api/playbook/jobs/{mine}")).status_code == 403
    await client.login("pj-o1@test.local")
    assert (await choose(client, mine, p, "complete", 0)).status_code == 200


async def test_offboard_to_the_end_with_marks_and_audit(admin, facts) -> None:  # type: ignore[no-untyped-def]
    await guide(admin, "offboard")
    jid = await mkjob(key="3551")
    p = await get(admin, jid)
    assert p["employee_id"] == "003551" and p["run"]["status"] == "needs_choice"
    assert facts.seen == [] or set(facts.seen) == {"003551"}
    r = await choose(admin, jid, p, "complete", 0)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["pinned"] and p["run"]["status"] == "needs_choice" and trace_ids(p)[-1] == "collect"
    p = (await choose(admin, jid, p, "collect", 0)).json()
    assert p["run"]["status"] == "outcome" and p["run"]["manual_left"] == 3
    assert {e["id"]: e["job_state"] for e in p["run"]["trace"] if e["kind"] == "job"}["biostar"] == "pending"
    # na needs a reason; only manual steps can be marked
    assert (await mark(admin, jid, p, "receive", "na")).json()["error"]["code"] == "reason_required"
    assert (await mark(admin, jid, p, "receive", "na", "ab")).status_code == 400
    assert (await mark(admin, jid, p, "complete")).json()["error"]["code"] == "not_manual_step"
    assert (await mark(admin, jid, p, "collect")).json()["error"]["code"] == "not_manual_step"
    assert (await mark(admin, jid, p, "nope")).status_code == 404
    p = (await mark(admin, jid, p, "receive")).json()
    e = next(e for e in p["run"]["trace"] if e["id"] == "receive")
    assert e["mark"]["status"] == "done" and e["mark"]["by"] == "admin@test.local" and e["mark"]["at"]
    p = (await mark(admin, jid, p, "reconcile", "na", "Không có gì để đối chiếu")).json()
    assert p["run"]["manual_left"] == 1 and p["run"]["path_complete"]
    p = (await mark(admin, jid, p, "review")).json()
    assert p["run"]["manual_left"] == 0 and p["run"]["path_complete"]
    q = ref(p)
    p = (await admin.delete(f"/api/playbook/jobs/{jid}/marks/review", params=q)).json()
    assert p["run"]["manual_left"] == 1
    assert (
        await audits("playbook.job_mark_set", jid) == 3
        and await audits("playbook.job_mark_cleared", jid) == 1
    )
    assert await audits("playbook.job_choice_set", jid) == 2
    # a choice can be dropped again
    p = (await admin.delete(f"/api/playbook/jobs/{jid}/choices/collect", params=q)).json()
    assert p["run"]["status"] == "needs_choice"
    assert (await choose(admin, jid, p, "collect", 5)).json()["error"]["code"] == "invalid_choice"


async def test_onboard_loop(admin) -> None:  # type: ignore[no-untyped-def]
    await guide(admin, "onboard")
    jid = await mkjob("lifecycle.onboard")
    p = await get(admin, jid)
    p = (await choose(admin, jid, p, "complete", 1)).json()
    assert trace_ids(p)[-3:] == ["complete", "ask_hr", "complete"] and p["run"]["status"] == "needs_choice"
    p = (await mark(admin, jid, p, "ask_hr")).json()
    n = await audits("playbook.job_choice_set", jid)
    p = (await choose(admin, jid, p, "complete", 1)).json()  # same pick while asked again: nothing changes
    assert await audits("playbook.job_choice_set", jid) == n and trace_ids(p)[-1] == "complete"
    p = (await choose(admin, jid, p, "complete", 0)).json()
    assert p["run"]["status"] == "needs_choice" and trace_ids(p)[-1] == "stock"
    assert p["run"]["off_path_marks"] == ["ask_hr"]  # ticked on the other branch: "nhánh khác", not counted
    p = (await choose(admin, jid, p, "stock", 0)).json()
    assert p["run"]["status"] == "outcome"


async def test_pin_version_and_races(admin) -> None:  # type: ignore[no-untyped-def]
    gid = await guide(admin, "offboard")
    j1, j2 = await mkjob(), await mkjob(key="003552")
    p1, p2 = await get(admin, j1), await get(admin, j2)
    assert (await choose(admin, j1, p1, "complete", 0)).status_code == 200  # pins v1
    assert (await admin.post(f"/api/playbook/guides/{gid}/publish", json={"note": "v2"})).status_code == 201
    p = await get(admin, j1)
    assert p["guide"]["version"] == 1 and p["pinned"] is True  # still v1
    assert (await get(admin, j2))["guide"]["version"] == 2 and p2["guide"]["version"] == 1
    r = await choose(admin, j2, p2, "complete", 0)  # shown v1, but v2 is current: no pin
    assert r.status_code == 409 and r.json()["error"]["code"] == "playbook_changed"
    assert (
        await choose(admin, j1, p | {"guide": {**p["guide"], "version": 2}}, "complete", 0)
    ).status_code == 409
    async with get_sessionmaker()() as s:
        assert await s.get(JobRun, uuid.UUID(j2)) is None
    # two first writes at once: both succeed (the Job row is locked), nothing 500s
    p2 = await get(admin, j2)
    a, b = await asyncio.gather(mark(admin, j2, p2, "receive"), mark(admin, j2, p2, "reconcile"))
    assert (a.status_code, b.status_code) == (200, 200)
    p2 = await get(admin, j2)
    assert {e["id"] for e in p2["run"]["trace"] if e.get("mark")} == {"receive", "reconcile"} or p2["run"][
        "status"
    ] == "needs_choice"
    async with get_sessionmaker()() as s:
        assert (
            await s.scalar(select(func.count()).select_from(JobRun).where(JobRun.job_id == uuid.UUID(j2)))
            == 1
        )


async def test_decisions_freeze_and_recheck(admin, facts) -> None:  # type: ignore[no-untyped-def]
    await guide(admin, "offboard")
    jid = await mkjob()
    p = await get(admin, jid)
    assert p["run"]["status"] == "needs_choice"
    async with get_sessionmaker()() as s:
        assert await s.get(JobRun, uuid.UUID(jid)) is None  # reading stores nothing
    p = (await choose(admin, jid, p, "complete", 0)).json()
    assert "collect" in trace_ids(p) and "recheck_assets" not in trace_ids(p)
    facts.value = "0"  # the data changed afterwards: the branch does not flip
    p = await get(admin, jid)
    assert "recheck_assets" not in trace_ids(p)
    assert next(e for e in p["run"]["trace"] if e["id"] == "has_laptop")["result"] is True
    r = await admin.delete(f"/api/playbook/jobs/{jid}/decisions/has_laptop", params=ref(p))
    assert r.status_code == 200, r.text
    assert "recheck_assets" in trace_ids(r.json())
    assert await audits("playbook.job_decision_dropped", jid) == 1
    facts.value = "1"
    assert "recheck_assets" in trace_ids(await get(admin, jid))  # re-decided and frozen again


async def test_cancelled_and_completed(admin) -> None:  # type: ignore[no-untyped-def]
    await guide(admin, "offboard")
    jid = await mkjob()
    p = await get(admin, jid)
    await setjob(jid, status="completed", closed_at=datetime.now(UTC))
    assert (await mark(admin, jid, p, "receive")).status_code == 200  # a finished Job can still be ticked
    await setjob(jid, status="cancelled")
    r = await mark(admin, jid, p, "reconcile")
    assert r.status_code == 409 and (await get(admin, jid))["can_edit"] is False


async def test_not_employee_workflow_loads_no_facts(admin, facts) -> None:  # type: ignore[no-untyped-def]
    g = await guide(admin, "offboard")
    ok = await admin.put(
        "/api/playbook/settings/job-guides", json={"workflow_guides": {"lifecycle.assign": g}}
    )
    assert ok.status_code == 200, ok.text
    jid = await mkjob("lifecycle.assign", key="003551")
    p = await get(admin, jid)
    p = (await choose(admin, jid, p, "complete", 0)).json()
    assert p["employee_id"] is None and p["run"]["status"] == "missing_data" and facts.seen == []


async def test_delete_guard_and_reopen(admin) -> None:  # type: ignore[no-untyped-def]
    gid = await guide(admin, "offboard")
    jid = await mkjob()
    p = await get(admin, jid)
    assert (await mark(admin, jid, p, "receive")).status_code == 200
    r = await admin.delete(f"/api/playbook/guides/{gid}")
    assert (
        r.status_code == 409
        and r.json()["error"]["code"] == "guide_in_use"
        and "1 Job" in r.json()["error"]["message"]
    )
    async with get_sessionmaker()() as s:
        job = await engine.get_job(s, jid)
        await engine.cancel_job(s, Actor(user_id=None, upn="t"), job, "x")  # type: ignore[call-arg]
        await s.commit()
    assert (await admin.delete(f"/api/playbook/guides/{gid}")).status_code == 204
    async with get_sessionmaker()() as s:
        job = await engine.get_job(s, jid)
        await engine.reopen_job(s, Actor(user_id=None, upn="t"), job, "x")  # type: ignore[call-arg]
        await s.commit()
    assert await get(admin, jid) == {"guide": None}


async def test_next_action(admin, client, facts) -> None:  # type: ignore[no-untyped-def]
    await guide(admin, "offboard")
    await _user(admin, "operator", "pj-na@test.local")
    await _user(admin, "operator", "pj-nb@test.local")
    jid = await mkjob(assignee="pj-na@test.local")
    facts.value = None  # the check cannot be decided: nothing a reminder can fix
    p = await get(admin, jid)
    p = (await choose(admin, jid, p, "complete", 0)).json()
    assert p["run"]["status"] == "missing_data"
    for e in [e for e in p["run"]["trace"] if e["manual"]]:  # the steps before the gap are done
        p = (await mark(admin, jid, p, e["id"])).json()
    assert p["run"]["manual_left"] == 0
    await setjob(jid, status="completed", closed_at=datetime.now(UTC))

    async def mine(upn: str) -> list[dict[str, Any]]:
        await client.login(upn)
        r = (await client.get("/api/core/me/next")).json()
        assert r["failed"] == []
        return [i for i in r["items"] if i["key"] == f"playbook:job:{jid}"]

    assert await mine("pj-na@test.local") == []  # C-15: missing data is not a next action
    facts.value = "1"
    p = (await choose(admin, jid, p, "complete", 0)).json()  # decides + freezes, stops at the choice
    assert p["run"]["status"] == "needs_choice"
    item = (await mine("pj-na@test.local"))[0]
    assert item["href"] == f"/lifecycle/jobs/{jid}" and item["title"] == "Playbook chưa đi hết nhánh"
    assert await mine("pj-nb@test.local") == []  # not the assignee
    await setjob(jid, closed_at=datetime.now(UTC) - timedelta(days=31))
    assert await mine("pj-na@test.local") == []
    await setjob(jid, closed_at=datetime.now(UTC))
    p = (await choose(admin, jid, p, "collect", 0)).json()
    assert (await mine("pj-na@test.local"))[0]["title"] == "Còn 1 bước tay trong Playbook"
    for node in ("receive", "reconcile", "review"):
        p = (await mark(admin, jid, p, node)).json()
    assert await mine("pj-na@test.local") == []  # gone after the last tick
    # an assignee who may not view the Job's workflow gets no reminder
    r = await admin.post(
        "/api/core/roles",
        json={"code": "pj_nv", "name": "x", "permissions": ["playbook.guides.view", "core.dashboard.view"]},
    )
    assert r.status_code == 201, r.text
    await _user(admin, "pj_nv", "pj-nv@test.local")
    j2 = await mkjob(key="003552", assignee="pj-nv@test.local")
    p2 = await get(admin, j2)
    await choose(admin, j2, p2, "complete", 0)
    await setjob(j2, status="completed", closed_at=datetime.now(UTC))
    await client.login("pj-nv@test.local")
    items = (await client.get("/api/core/me/next")).json()["items"]
    assert not [i for i in items if i["key"] == f"playbook:job:{j2}"]


async def test_pin_audit_unpinned_delete_and_decision_shape(admin, facts) -> None:  # type: ignore[no-untyped-def]
    await guide(admin, "offboard")
    jid = await mkjob()
    p = await get(admin, jid)
    q = ref(p)
    # C-16: undoing on an unpinned Job pins nothing; a wrong version is still refused
    r = await admin.delete(f"/api/playbook/jobs/{jid}/marks/receive", params=q)
    assert r.status_code == 200 and r.json()["pinned"] is False and r.json()["guide"]["id"] == q["guide_id"]
    for path in ("choices/complete", "decisions/has_laptop"):
        r = await admin.delete(f"/api/playbook/jobs/{jid}/{path}", params=q)
        assert r.status_code == 200 and r.json()["pinned"] is False
    r = await admin.delete(f"/api/playbook/jobs/{jid}/marks/receive", params=q | {"version": 9})
    assert r.status_code == 409 and r.json()["error"]["code"] == "playbook_changed"
    async with get_sessionmaker()() as s:
        assert await s.get(JobRun, uuid.UUID(jid)) is None
    assert await audits("playbook.job_pinned", jid) == 0
    # the first write pins once, audited with guide + version
    p = (await choose(admin, jid, p, "complete", 0)).json()
    await choose(admin, jid, p, "collect", 0)
    assert await audits("playbook.job_pinned", jid) == 1
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "playbook.job_pinned",
                                                    AuditLog.target_id == jid))  # fmt: skip
        assert row and row.after == {"guide_id": q["guide_id"], "version": q["version"]}
        run = await s.get(JobRun, uuid.UUID(jid))
        assert run and run.decisions["has_laptop"]["frozen_at"] and "at" not in run.decisions["has_laptop"]
    # C-18: the response of the write that froze shows the frozen decision
    p = await get(admin, jid)
    assert next(e for e in p["run"]["trace"] if e["id"] == "has_laptop")["frozen_at"]
    # C-19: ticks are rows; clearing removes them
    p = (await mark(admin, jid, p, "receive")).json()
    await admin.delete(f"/api/playbook/jobs/{jid}/marks/receive", params=ref(p))
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(JobMark).where(
            JobMark.job_id == uuid.UUID(jid))) == 0  # fmt: skip


async def test_frozen_decision_is_json_safe() -> None:
    from decimal import Decimal

    from app.modules.playbook.jobs_api import _frozen

    d = {"branch": 0, "left": {"value": Decimal("1.5"), "source": "s"}, "right": {"value": 2}, "result": True}
    out = _frozen(d, "now")
    assert out["left"]["value"] == "1.5" and out["right"]["value"] == 2 and out["frozen_at"] == "now"
    assert _frozen({"left": None, "right": None}, "n")["left"] is None


async def test_job_busy_when_row_is_locked(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.modules.playbook import jobs_api

    await guide(admin, "offboard")
    jid = await mkjob()
    p = await get(admin, jid)
    monkeypatch.setattr(jobs_api, "LOCK_TIMEOUT", "200ms")
    async with get_sessionmaker()() as holder:  # the worker running a step holds the Job row
        await holder.execute(select(Job).where(Job.id == uuid.UUID(jid)).with_for_update())
        r = await choose(admin, jid, p, "complete", 0)
        await holder.rollback()
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_busy"
    assert (await choose(admin, jid, p, "complete", 0)).status_code == 200  # free again


async def test_audit_lock_wait_is_not_bounded_by_the_job_lock_timeout(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    from sqlalchemy import text

    from app.core.audit.service import _AUDIT_LOCK_KEY
    from app.modules.playbook import jobs_api

    await guide(admin, "offboard")
    jid = await mkjob()
    p = await get(admin, jid)
    monkeypatch.setattr(jobs_api, "LOCK_TIMEOUT", "200ms")
    async with get_sessionmaker()() as holder:  # another transaction is writing its audit entry
        await holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})

        async def release() -> None:
            await asyncio.sleep(0.6)
            await holder.rollback()

        r, _ = await asyncio.gather(choose(admin, jid, p, "complete", 0), release())
    assert r.status_code == 200, r.text
