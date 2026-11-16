"""Application factory: mounts the platform core and every enabled module."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

import app.core.models  # noqa: F401  (register metadata)
from app.core.audit.api import router as audit_router
from app.core.auth.api import router as auth_router
from app.core.config import Settings, get_settings
from app.core.connections.api import router as connections_router
from app.core.connectors.registry import load_builtin_connectors
from app.core.dashboard.api import router as dashboard_router
from app.core.db import dispose_engine, get_sessionmaker
from app.core.documents.api import router as documents_router
from app.core.documents.service import ensure_default_templates
from app.core.errors import install_error_handlers
from app.core.logging import CorrelationIdMiddleware, configure_logging, get_logger
from app.core.modules.registry import ModuleRegistry, build_registry, set_registry
from app.core.rbac.api import router as rbac_router
from app.core.rbac.service import sync_permissions
from app.core.scheduler.service import sync_tasks
from app.core.secrets.crypto import get_keyring

log = get_logger(__name__)


async def startup(registry: ModuleRegistry) -> None:
    async with get_sessionmaker()() as session:
        new = await sync_permissions(session, registry)
        await sync_tasks(session, registry)
        await ensure_default_templates(session, registry)
        await session.commit()
    if new:
        log.info("permissions_registered", codes=new)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    load_builtin_connectors()
    registry = build_registry(settings.enabled_modules)
    set_registry(registry)
    get_keyring()  # fail fast on a missing or malformed master key

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await startup(registry)
        log.info("app_started", modules=[m.id for m in registry.modules], auth_mode=settings.auth_mode)
        yield
        await dispose_engine()

    app = FastAPI(
        title="infra-ops",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.environment != "production" else None,
        openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    )
    app.add_middleware(CorrelationIdMiddleware)
    install_error_handlers(app)

    core = APIRouter(prefix="/api/core")
    for r in (auth_router, rbac_router, connections_router, audit_router, dashboard_router, documents_router):
        core.include_router(r)
    app.include_router(core)
    for manifest in registry.modules:
        for router in manifest.routers:
            app.include_router(router, prefix=f"/api/{manifest.id}")

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        try:
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
        return JSONResponse({"status": "ok", "database": "up"})

    return app
