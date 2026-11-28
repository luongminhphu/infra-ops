"""Background worker: ``python -m app.worker``.

Runs scheduled tasks declared by enabled modules. Safe to run several replicas.
"""

from __future__ import annotations

import asyncio
import signal

import app.core.models  # noqa: F401
from app.core.config import get_settings
from app.core.connectors.registry import load_builtin_connectors
from app.core.core_manifest import CORE_MANIFEST
from app.core.db import dispose_engine, get_sessionmaker
from app.core.logging import configure_logging, get_logger
from app.core.modules.registry import build_registry, set_registry
from app.core.scheduler.service import sync_tasks, worker_loop
from app.core.secrets.crypto import get_keyring

log = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    load_builtin_connectors()
    registry = build_registry(CORE_MANIFEST, settings.enabled_modules)
    set_registry(registry)
    get_keyring()
    maker = get_sessionmaker()
    async with maker() as session:
        await sync_tasks(session, registry)
        await session.commit()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker_started", tasks=[t.key for _, t in registry.scheduled_tasks()])
    await worker_loop(maker, registry, stop=stop)
    await dispose_engine()
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
