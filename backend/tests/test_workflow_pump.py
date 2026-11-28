"""pump(): locks only jobs with due work and never advances a job that left ACTIVE."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import event, select

from app.core.db import get_engine, get_sessionmaker
from app.core.workflow import engine as wf
from app.core.workflow.engine import pump, today_vn
from app.core.workflow.models import Job, JobStep


def make(tag: str, status: str, step_status: str, kind: str = "manual", **kw) -> Job:  # type: ignore[no-untyped-def]
    return Job(
        workflow_key="lifecycle.onboard",
        module_id="lifecycle",
        status=status,
        subject_key=f"pump-{tag}",
        subject_label="x",
        idempotency_key=f"test:pump:{tag}",
        params={},
        created_by="test",
        steps=[JobStep(seq=1, key="biostar_enroll", title="BioStar", kind=kind, status=step_status)],
        evidence=[],
        assignments=[],
        **kw,
    )


async def _cleanup(ids) -> None:  # type: ignore[no-untyped-def]
    async with get_sessionmaker()() as s:
        await s.execute(Job.__table__.delete().where(Job.id.in_(ids)))
        await s.commit()


async def test_waiting_job_without_due_work_is_not_locked(admin) -> None:  # type: ignore[no-untyped-def]
    async with get_sessionmaker()() as s:
        j = make("idle", "waiting", "waiting")
        s.add(j)
        await s.commit()
        jid = j.id
    locks: list[str] = []

    def spy(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        if "FOR UPDATE" in statement and str(jid) in repr(params):
            locks.append(statement)

    sync_engine = get_engine().sync_engine
    event.listen(sync_engine, "before_cursor_execute", spy)
    try:
        async with get_sessionmaker()() as s:
            await pump(s)
    finally:
        event.remove(sync_engine, "before_cursor_execute", spy)
        await _cleanup([jid])
    assert locks == []


async def test_job_cancelled_between_read_and_lock_is_not_advanced(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with get_sessionmaker()() as s:
        j = make("cancel", "running", "pending", kind="auto")
        s.add(j)
        await s.commit()
        jid = j.id
    ran: list[str] = []

    async def fake_advance(session, job, actor):  # type: ignore[no-untyped-def]
        ran.append(job.subject_key)

    async def cancel_now() -> None:
        async with get_sessionmaker()() as s2:
            await s2.execute(Job.__table__.update().where(Job.id == jid).values(status="cancelled"))
            await s2.commit()

    # Cancel right after the candidate read: hook the first statement that locks this job.
    fired: dict[str, bool] = {}
    monkeypatch.setattr(wf, "advance", fake_advance)
    orig_scalar = None
    try:
        async with get_sessionmaker()() as s:
            orig_scalar = s.scalar

            async def scalar(stmt, *a, **k):  # type: ignore[no-untyped-def]
                if "FOR UPDATE" in str(stmt) and not fired.get("cancelled"):
                    fired["cancelled"] = True
                    await cancel_now()
                return await orig_scalar(stmt, *a, **k)

            monkeypatch.setattr(s, "scalar", scalar)
            await pump(s)
        async with get_sessionmaker()() as s:
            status = await s.scalar(select(Job.status).where(Job.id == jid))
    finally:
        await _cleanup([jid])
    assert fired.get("cancelled") and ran == [] and status == "cancelled"


async def test_running_and_due_scheduled_jobs_advance(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with get_sessionmaker()() as s:
        a = make("run", "running", "pending", kind="auto")
        b = make("sched", "scheduled", "pending", kind="auto", scheduled_for=today_vn() - timedelta(days=0))
        s.add_all([a, b])
        await s.commit()
        ids = [a.id, b.id]
    seen: list[tuple[str, str]] = []

    async def fake_advance(session, job, actor):  # type: ignore[no-untyped-def]
        seen.append((job.subject_key, job.status))

    monkeypatch.setattr(wf, "advance", fake_advance)
    try:
        async with get_sessionmaker()() as s:
            out = await pump(s)
    finally:
        await _cleanup(ids)
    assert ("pump-run", "running") in seen and ("pump-sched", "running") in seen
    assert out["advanced"] >= 2
