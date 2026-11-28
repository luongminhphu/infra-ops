"""Global search: accent-free match, per-source row scope, hidden fields, failing sources (C-07)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.audit.service import Actor
from app.core.auth.models import User
from app.core.db import get_sessionmaker
from app.core.documents import service
from app.core.modules.registry import get_registry
from app.core.workflow import engine
from app.modules.lifecycle.documents import HANDOVER
from app.modules.lifecycle.models import LifecycleRequest
from app.modules.playbook.models import Guide
from app.modules.workload.models import PurchaseRequest, Technician, Ticket
from tests.test_documents import handover_payload
from tests.test_workload import _user

SYSTEM = Actor(user_id=None, upn="system", ip=None)


async def _uid(upn: str) -> User:
    async with get_sessionmaker()() as s:
        return await s.scalar(select(User).where(User.upn == upn))  # type: ignore[return-value]


_TAGS: list[str] = []


@pytest.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    """Other tests count tickets, purchases and technicians: leave none of ours behind."""
    yield
    async with get_sessionmaker()() as s:
        for tag in _TAGS:
            await s.execute(delete(Ticket).where(Ticket.id == tag))
            await s.execute(delete(LifecycleRequest).where(LifecycleRequest.id == tag))
            await s.execute(delete(Guide).where(Guide.title.contains(tag)))
            await s.execute(delete(PurchaseRequest).where(PurchaseRequest.item.contains(tag)))
            await s.execute(delete(Technician).where(Technician.id == f"T{tag}"[:32]))
        await s.commit()
    _TAGS.clear()


async def _seed(owner: User, tag: str) -> dict[str, str]:
    """A Job, a record, a task and a purchase proposal that belong to `owner`, all carrying `tag`."""
    _TAGS.append(tag)
    async with get_sessionmaker()() as s:
        job, _ = await engine.create_job(
            s, SYSTEM, "lifecycle.onboard", params={"employee_id": "009003"},
            subject_key="009003", subject_label=f"Nguyễn {tag}", external_ref=None,
        )  # fmt: skip
        await engine.set_assignee(
            s, job, user_id=owner.id, name=owner.display_name, actor="test", source="manual"
        )
        doc = await service.create_draft(
            s, SYSTEM, HANDOVER, "lifecycle", handover_payload(name=f"Nguyễn {tag}"), job_id=job.id
        )
        mine = await s.scalar(select(Technician).where(Technician.user_id == owner.id))
        tech = mine.id if mine else f"T{tag}"[:32]
        if mine is None:
            s.add(Technician(id=tech, name="Op", active=True, user_id=owner.id))
            await s.flush()  # the ticket's FK needs the technician row first
        s.add(Ticket(id=tag, display_id=tag, subject=f"Máy hỏng {tag}", technician_id=tech, status="Open",
                     state="open", created_at=datetime(2020, 1, 1, tzinfo=UTC)))  # fmt: skip
        s.add(PurchaseRequest(kind="new", item=f"Chuột {tag}", created_by=owner.upn, reason=f"lý do {tag}x"))
        await s.commit()
        return {"job": str(job.id), "doc": str(doc.id)}


def _hrefs(body: dict[str, Any]) -> dict[str, list[str]]:
    return {g["key"]: [i["href"] for i in g["items"]] for g in body["groups"]}


async def test_accent_free_match_and_operator_scope(admin, client) -> None:  # type: ignore[no-untyped-def]
    await _user(admin, "operator", "sr-a@test.local")
    await _user(admin, "operator", "sr-b@test.local")
    await _user(admin, "supervisor", "sr-sup@test.local")
    tag = "zq" + uuid.uuid4().hex[:6]
    ids = await _seed(await _uid("sr-a@test.local"), tag)

    await client.login("sr-a@test.local")
    body = (await client.get("/api/core/search", params={"q": f"nguyen {tag.upper()}"})).json()
    got = _hrefs(body)
    assert got["core.jobs"] == [f"/lifecycle/jobs/{ids['job']}"]  # "nguyen" finds "Nguyễn"
    assert got["core.documents"] == [f"/lifecycle/documents/{ids['doc']}"]
    assert body["failed"] == []
    got = _hrefs((await client.get("/api/core/search", params={"q": f"may hong {tag}"})).json())
    assert got["workload.tickets"] == [f"/workload/tickets?state=all&q={tag}"]
    got = _hrefs((await client.get("/api/core/search", params={"q": f"chuot {tag}"})).json())
    assert len(got["workload.purchases"]) == 1

    await client.login("sr-b@test.local")  # another operator: nothing of A's, in any source
    for q in (f"nguyen {tag}", f"may hong {tag}", f"chuot {tag}"):
        assert (await client.get("/api/core/search", params={"q": q})).json()["groups"] == []

    await client.login("sr-sup@test.local")  # view_all sees them
    got = _hrefs((await client.get("/api/core/search", params={"q": tag})).json())
    assert {"core.jobs", "core.documents", "workload.tickets", "workload.purchases"} <= set(got)


async def _min_user(admin) -> None:  # type: ignore[no-untyped-def]
    """A user whose role has only the dashboard and Job view (idempotent)."""
    role = await admin.post(
        "/api/core/roles",
        json={
            "code": "sr_min",
            "name": "Search min",
            "permissions": ["core.dashboard.view", "lifecycle.job.view"],
        },
    )
    assert role.status_code in (201, 409), role.text
    roles = {x["code"]: x["id"] for x in (await admin.get("/api/core/roles")).json()}
    await admin.post(
        "/api/core/users",
        json={"upn": "sr-min@test.local", "display_name": "m", "role_ids": [roles["sr_min"]]},
    )


async def test_hidden_fields_not_matched_and_no_permission_no_group(admin, client) -> None:  # type: ignore[no-untyped-def]
    await _min_user(admin)
    tag = "hf" + uuid.uuid4().hex[:6]
    await _seed(await _uid("admin@test.local"), tag)
    # The purchase reason is not shown in (nor matched by) the list, so search cannot be an oracle for it.
    got = _hrefs((await admin.get("/api/core/search", params={"q": f"lý do {tag}x"})).json())
    assert "workload.purchases" not in got
    got = _hrefs((await admin.get("/api/core/search", params={"q": f"chuột {tag}"})).json())
    assert "workload.purchases" in got

    await client.login("sr-min@test.local")  # only Jobs: no task, purchase, request, guide or record group
    body = (await client.get("/api/core/search", params={"q": tag})).json()
    assert {g["key"] for g in body["groups"]} <= {"core.jobs"} and body["failed"] == []


async def test_failing_source_reported_others_returned_short_query_skipped(admin) -> None:  # type: ignore[no-untyped-def]
    tag = "fs" + uuid.uuid4().hex[:6]
    await _seed(await _uid("admin@test.local"), tag)
    calls: list[str] = []

    async def boom(*_: Any) -> list[dict[str, Any]]:
        calls.append("boom")
        raise RuntimeError("secret detail")

    src = next(x for m in get_registry().modules for x in m.search_sources if x.key == "workload.tickets")
    original = src.search
    object.__setattr__(src, "search", boom)  # frozen dataclass
    try:
        body = (await admin.get("/api/core/search", params={"q": tag})).json()
        assert calls == ["boom"] and "secret" not in str(body)
        assert len(body["failed"]) == 1  # the module name, no raw error
        assert "core.jobs" in _hrefs(body) and "workload.tickets" not in _hrefs(body)
        calls.clear()
        assert (await admin.get("/api/core/search", params={"q": "a"})).json() == {"groups": [], "failed": []}
        assert (await admin.get("/api/core/search", params={"q": " Đ "})).json()["groups"] == []
        assert calls == []
    finally:
        object.__setattr__(src, "search", original)


async def test_operator_with_technician_sees_only_own_tickets(admin, client) -> None:  # type: ignore[no-untyped-def]
    await _user(admin, "operator", "sr-c@test.local")
    await _user(admin, "operator", "sr-d@test.local")
    tag_c, tag_d = "oc" + uuid.uuid4().hex[:6], "od" + uuid.uuid4().hex[:6]
    await _seed(await _uid("sr-c@test.local"), tag_c)
    await _seed(await _uid("sr-d@test.local"), tag_d)  # D now has a Technician row and an own ticket

    await client.login("sr-d@test.local")
    own = _hrefs((await client.get("/api/core/search", params={"q": f"may hong {tag_d}"})).json())
    assert own["workload.tickets"] == [f"/workload/tickets?state=all&q={tag_d}"]
    other = _hrefs((await client.get("/api/core/search", params={"q": f"may hong {tag_c}"})).json())
    assert "workload.tickets" not in other  # fails if the technician filter is dropped


async def test_unlisted_fields_not_matched_and_request_group_needs_permission(admin, client) -> None:  # type: ignore[no-untyped-def]
    tag = "rq" + uuid.uuid4().hex[:6]
    _TAGS.append(tag)
    async with get_sessionmaker()() as s:
        s.add(LifecycleRequest(id=tag, display_id=tag, subject=f"Nhân sự mới {tag}", status="Open",
                               description_text=f"mô tả ẩn {tag}z"))  # fmt: skip
        s.add(
            Ticket(
                id=tag, display_id=tag, subject="x", group_name=f"nhóm ẩn {tag}z", status="Open", state="open"
            )
        )
        await s.commit()
    # Description / group are not in the lists, so they cannot be searched.
    got = _hrefs((await admin.get("/api/core/search", params={"q": f"{tag}z"})).json())
    assert "lifecycle.requests" not in got and "workload.tickets" not in got
    got = _hrefs((await admin.get("/api/core/search", params={"q": f"nhan su moi {tag}"})).json())
    assert got["lifecycle.requests"] == [f"/lifecycle/requests?open={tag}"]  # accent-free

    await _min_user(admin)
    await client.login("sr-min@test.local")  # role without lifecycle.reconciliation.view
    body = (await client.get("/api/core/search", params={"q": tag})).json()
    assert "lifecycle.requests" not in _hrefs(body)


async def test_guide_draft_only_for_editors(admin, client) -> None:  # type: ignore[no-untyped-def]
    await _user(admin, "viewer", "sr-v@test.local")
    tag = "gd" + uuid.uuid4().hex[:6]
    _TAGS.append(tag)
    r = await admin.post("/api/playbook/guides", json={"title": f"Hướng dẫn {tag}", "category": "Mạng"})
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    got = _hrefs((await admin.get("/api/core/search", params={"q": f"huong dan {tag}"})).json())
    assert got["playbook.guides"] == [f"/playbook/{gid}/edit"]
    await client.login("sr-v@test.local")
    body = (await client.get("/api/core/search", params={"q": tag})).json()
    assert "playbook.guides" not in _hrefs(body)


async def test_slow_source_times_out_and_next_source_returns(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    from app.core.search import api as search_api

    tag = "to" + uuid.uuid4().hex[:6]
    await _seed(await _uid("admin@test.local"), tag)

    async def slow(*_: Any) -> list[dict[str, Any]]:
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(search_api, "SOURCE_TIMEOUT", 0.05)
    src = next(x for m in get_registry().modules for x in m.search_sources if x.key == "workload.tickets")
    original = src.search
    object.__setattr__(src, "search", slow)
    try:
        body = (await admin.get("/api/core/search", params={"q": tag})).json()
    finally:
        object.__setattr__(src, "search", original)
    assert len(body["failed"]) == 1
    assert "workload.tickets" not in _hrefs(body) and "workload.purchases" in _hrefs(body)
