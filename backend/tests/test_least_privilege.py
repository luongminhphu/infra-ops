"""Least privilege: an Operator sees only their own and unassigned jobs, never higher-level data."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.audit.service import Actor
from app.core.auth.models import User
from app.core.db import get_sessionmaker
from app.core.workflow import engine
from tests.test_workload import _user


async def _job(label: str, assignee: User | None) -> str:
    async with get_sessionmaker()() as s:
        job, _ = await engine.create_job(
            s, Actor(user_id=None, upn="system", ip=None), "lifecycle.onboard",
            params={"employee_id": "009001"}, subject_key="009001", subject_label=label, external_ref=None,
        )  # fmt: skip
        if assignee:
            await engine.set_assignee(
                s, job, user_id=assignee.id, name=assignee.display_name, actor="test", source="manual"
            )
        await s.commit()
        return str(job.id)


async def test_operator_sees_own_and_unassigned_jobs_only(admin, client) -> None:  # type: ignore[no-untyped-def]
    await _user(admin, "operator", "lp-op1@test.local")
    await _user(admin, "operator", "lp-op2@test.local")
    async with get_sessionmaker()() as s:
        op1 = await s.scalar(select(User).where(User.upn == "lp-op1@test.local"))
        op2 = await s.scalar(select(User).where(User.upn == "lp-op2@test.local"))
    tag = uuid.uuid4().hex[:6]
    mine, theirs, free = (
        await _job(f"mine {tag}", op1),
        await _job(f"theirs {tag}", op2),
        await _job(f"free {tag}", None),
    )

    await client.login("lp-op1@test.local")
    seen = {j["id"] for j in (await client.get("/api/core/jobs", params={"q": tag})).json()["items"]}
    assert seen == {mine, free}
    assert (await client.get(f"/api/core/jobs/{theirs}")).status_code == 403
    assert (await client.get(f"/api/core/jobs/{free}")).status_code == 200
    # Hand-overs and stock changes are not an Operator's business; nor are team-wide counts.
    flows = {w["key"] for w in (await client.get("/api/core/workflows?module=lifecycle")).json()}
    assert "lifecycle.assign" not in flows and "lifecycle.asset_dispose" not in flows
    assert all(not w["history"] for w in (await client.get("/api/core/workflows?module=lifecycle")).json())
    widgets = {w["kind"] for w in (await client.get("/api/core/dashboard")).json()}
    assert not widgets & {"lifecycle_jobs", "connections", "expiring_secrets"}

    # Connections: whether they work, not the account names, secret hints or the shared Write credential.
    for c in (await client.get("/api/core/connections")).json():
        assert c["credentials"]["write"] is None
        assert c["credentials"]["read"] is None or (
            c["credentials"]["read"]["public"] == {} and c["credentials"]["read"]["hint"] is None
        )
        assert (
            await client.post(f"/api/core/connections/{c['id']}/test", params={"purpose": "write"})
        ).status_code == 403

    # A Supervisor sees the team's jobs.
    await _user(admin, "supervisor", "lp-sup@test.local")
    await client.login("lp-sup@test.local")
    seen = {j["id"] for j in (await client.get("/api/core/jobs", params={"q": tag})).json()["items"]}
    assert seen == {mine, theirs, free}
