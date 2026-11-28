"""PostgreSQL-backed scheduler (no Redis): periodic tasks declared in module manifests.

Rows in ``core_scheduled_task`` hold timing and last result. A worker leases one due task at a
time (``FOR UPDATE SKIP LOCKED`` + ``lease_until``), so several worker replicas never run the same
task concurrently and a crashed worker's lease simply expires.
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import NotFound
from app.core.logging import correlation_id_var, get_logger
from app.core.modules.manifest import ScheduledTask
from app.core.modules.registry import ModuleRegistry
from app.core.scheduler.models import ScheduledTaskRow

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


# api and worker (and their replicas) start together: startup writes are serialised with this lock.
STARTUP_LOCK_KEY = 0x1F0A_5717


async def startup_lock(session: AsyncSession) -> None:
    """Transaction-scoped; re-entrant within the same transaction."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": STARTUP_LOCK_KEY})


async def sync_tasks(session: AsyncSession, registry: ModuleRegistry) -> None:
    """Insert rows for newly declared tasks. Existing rows keep admin-edited interval/enabled."""
    await startup_lock(session)
    existing = set((await session.scalars(select(ScheduledTaskRow.key))).all())
    for module_id, task in registry.scheduled_tasks():
        if task.key not in existing:
            session.add(
                ScheduledTaskRow(
                    key=task.key,
                    module_id=module_id,
                    interval_seconds=task.interval_seconds,
                    enabled=task.enabled_by_default,
                    next_run_at=_now() + timedelta(seconds=task.initial_delay_seconds),
                )
            )
    await session.flush()


async def request_run(session: AsyncSession, key: str, requested_by: str | None) -> ScheduledTaskRow:
    """Ask the worker to run a task as soon as possible (manual "Sync now")."""
    row = await session.get(ScheduledTaskRow, key)
    if row is None:
        raise NotFound("Không tìm thấy tác vụ nền.")
    row.next_run_at = _now()
    row.requested_by = requested_by
    await session.flush()
    return row


async def get_task(session: AsyncSession, key: str) -> ScheduledTaskRow | None:
    return await session.get(ScheduledTaskRow, key)


async def _lease_due(session: AsyncSession, known: set[str], lease_seconds: int) -> ScheduledTaskRow | None:
    now = _now()
    stmt = (
        select(ScheduledTaskRow)
        .where(
            ScheduledTaskRow.enabled.is_(True),
            ScheduledTaskRow.next_run_at <= now,
            ScheduledTaskRow.key.in_(known),
            or_(ScheduledTaskRow.lease_until.is_(None), ScheduledTaskRow.lease_until < now),
        )
        .order_by(ScheduledTaskRow.next_run_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    row = await session.scalar(stmt)
    if row is None:
        return None
    row.lease_until = now + timedelta(seconds=lease_seconds)
    row.last_started_at = now
    row.last_status = "running"
    row.last_error = None
    await session.commit()
    return row


async def run_once(maker: async_sessionmaker[AsyncSession], registry: ModuleRegistry) -> str | None:
    """Run at most one due task. Returns its key, or None when nothing was due."""
    tasks: dict[str, ScheduledTask] = {t.key: t for _, t in registry.scheduled_tasks()}
    async with maker() as session:
        row = await _lease_due(session, set(tasks), lease_seconds=3600)
        if row is None:
            return None
        key = row.key
        task = tasks[key]
    token = correlation_id_var.set(f"task-{key}-{int(_now().timestamp())}")
    status, error, result = "success", None, None
    try:
        async with maker() as session:
            result = await asyncio.wait_for(task.handler(session), timeout=task.timeout_seconds)
            await session.commit()
    except Exception as exc:
        status = "failure"
        error = f"{type(exc).__name__}: {exc}"[:2000]
        log.error("scheduled_task_failed", task=key, error=error, trace=traceback.format_exc(limit=5))
    finally:
        correlation_id_var.reset(token)
    async with maker() as session:
        row = await session.get(ScheduledTaskRow, key, with_for_update=True)
        if row is not None:
            now = _now()
            row.last_finished_at = now
            row.last_status = status
            row.last_error = error
            row.last_result = result
            row.lease_until = None
            row.requested_by = None
            # Keep a manual request made while running; otherwise schedule the next interval.
            if row.next_run_at <= (row.last_started_at or now):
                row.next_run_at = now + timedelta(seconds=row.interval_seconds)
            await session.commit()
    log.info("scheduled_task_done", task=key, status=status)
    return key


async def worker_loop(
    maker: async_sessionmaker[AsyncSession],
    registry: ModuleRegistry,
    poll_seconds: float = 5.0,
    stop: asyncio.Event | None = None,
) -> None:
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            ran = await run_once(maker, registry)
        except Exception:
            log.exception("worker_iteration_failed")
            ran = None
        if ran is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass


def task_out(row: ScheduledTaskRow, *, show_error: bool = False) -> dict[str, Any]:
    return {
        "key": row.key,
        "module_id": row.module_id,
        "interval_seconds": row.interval_seconds,
        "enabled": row.enabled,
        "next_run_at": row.next_run_at,
        "running": row.last_status == "running" and bool(row.lease_until and row.lease_until > _now()),
        "last_started_at": row.last_started_at,
        "last_finished_at": row.last_finished_at,
        "last_status": row.last_status,
        # Raw errors may carry hostnames/DNs/SQL: only privileged callers see them.
        "last_error": row.last_error
        if show_error or not row.last_error
        else "Lần chạy gần nhất lỗi – xem nhật ký hệ thống.",
        "last_result": row.last_result,
    }
