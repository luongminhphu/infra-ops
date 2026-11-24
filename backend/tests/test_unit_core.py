import pytest
from fastapi import APIRouter

from app.core.auth.passwords import hash_password, password_problems, verify_password
from app.core.logging import REDACTED, redact
from app.core.modules.manifest import ModuleManifest, NavItem, Permission
from app.core.modules.registry import ModuleRegistry, load_manifest


def test_redact_nested_secret_keys() -> None:
    data = {
        "name": "x",
        "config": {"password": "p", "items": [{"api_key": "k", "ok": 1}]},
        "Authorization": "b",
    }
    out = redact(data)
    assert out["name"] == "x"
    assert out["config"]["password"] == REDACTED
    assert out["config"]["items"][0] == {"api_key": REDACTED, "ok": 1}
    assert out["Authorization"] == REDACTED


def test_password_hash_and_verify() -> None:
    h = hash_password("Correct-Horse-9")
    assert verify_password("Correct-Horse-9", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_password_policy() -> None:
    assert password_problems("Abcdefghij1!", 12) == []
    assert len(password_problems("short", 12)) == 2


def test_permission_must_be_prefixed() -> None:
    with pytest.raises(ValueError):
        ModuleManifest(id="assets", name="A", permissions=[Permission("lifecycle.x.y", "bad")])


def test_registry_rejects_duplicate_permission() -> None:
    a = ModuleManifest(id="a", name="A", permissions=[Permission("a.x.view", "x")])
    with pytest.raises(ValueError):
        ModuleRegistry([a, a])


def test_nav_filtered_by_permission() -> None:
    m = ModuleManifest(
        id="a",
        name="A",
        routers=[APIRouter()],
        permissions=[Permission("a.x.view", "x"), Permission("a.y.view", "y")],
        nav=[NavItem("X", "/x", "box", "a.x.view"), NavItem("Y", "/y", "box", "a.y.view")],
    )
    reg = ModuleRegistry([m])
    nav = reg.nav_for({"a.x.view"})
    assert [i["path"] for i in nav[0]["items"]] == ["/x"]  # type: ignore[index]
    assert reg.nav_for(set()) == []


def test_load_lifecycle_manifest() -> None:
    m = load_manifest("lifecycle")
    assert m.id == "lifecycle"
    assert "sdp" in m.connectors_used


@pytest.mark.parametrize("bad", ["../etc", "not-a-module"])
def test_load_manifest_rejects_bad_ids(bad: str) -> None:
    with pytest.raises((ValueError, ModuleNotFoundError)):
        load_manifest(bad)


def test_scheduled_task_must_be_prefixed() -> None:
    from app.core.modules.manifest import ScheduledTask

    async def noop(_):  # type: ignore[no-untyped-def]
        return {}

    with pytest.raises(ValueError):
        ModuleManifest(id="a", name="A", scheduled_tasks=[ScheduledTask("b.sync", "x", noop)])


async def test_concurrent_startup_does_not_collide(app) -> None:  # type: ignore[no-untyped-def]
    """api and worker start together on a fresh database: both register tasks and permissions."""
    import asyncio

    from sqlalchemy import delete, func, select

    from app.core.db import get_sessionmaker
    from app.core.modules.registry import get_registry
    from app.core.scheduler.models import ScheduledTaskRow
    from app.core.scheduler.service import sync_tasks
    from app.main import startup

    async with get_sessionmaker()() as s:
        await s.execute(delete(ScheduledTaskRow))
        await s.commit()

    async def worker_start() -> None:
        async with get_sessionmaker()() as s:
            await sync_tasks(s, get_registry())
            await s.commit()

    await asyncio.gather(startup(get_registry()), worker_start(), startup(get_registry()), worker_start())
    async with get_sessionmaker()() as s:
        n = await s.scalar(select(func.count()).select_from(ScheduledTaskRow))
    assert n == len(get_registry().scheduled_tasks())


def test_running_job_without_progress_is_flagged_stalled() -> None:
    from datetime import UTC, datetime, timedelta

    from app.core.workflow.api import _stalled_minutes
    from app.core.workflow.models import Job

    now = datetime.now(UTC)
    assert _stalled_minutes(Job(status="running", updated_at=now - timedelta(minutes=2))) is None
    assert _stalled_minutes(Job(status="running", updated_at=now - timedelta(hours=15))) == 900
    # Waiting for a person is not "stalled": only running jobs are advanced by the worker.
    assert _stalled_minutes(Job(status="waiting", updated_at=now - timedelta(hours=15))) is None
