"""Test setup. Requires a PostgreSQL database (never an external system).

Set INFRAOPS_TEST_DATABASE_URL, default: local socket on port 5433 (see README).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

TEST_DB = os.environ.get(
    "INFRAOPS_TEST_DATABASE_URL", "postgresql+asyncpg://infraops:infraops@/infraops_test?host=/tmp&port=5433"
)
os.environ.update(
    {
        "INFRAOPS_ENVIRONMENT": "test",
        "INFRAOPS_DATABASE_URL": TEST_DB,
        "INFRAOPS_MASTER_KEYS": "v1:MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        "INFRAOPS_AUTH_MODE": "dev",
        "INFRAOPS_BOOTSTRAP_ADMIN_UPNS": "admin@test.local",
        "INFRAOPS_SESSION_COOKIE_SECURE": "false",
        "INFRAOPS_SIGNING_SECRET": "x" * 40,
        "INFRAOPS_LOG_JSON": "false",
        "INFRAOPS_LOG_LEVEL": "WARNING",
        "INFRAOPS_STORAGE_DIR": tempfile.mkdtemp(prefix="infraops-test-files-"),
    }
)

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import dispose_engine  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    subprocess.run(  # noqa: ASYNC221 - one-off migration before tests
        [sys.executable, "-m", "alembic", "upgrade", "heads"], cwd=BACKEND, check=True, capture_output=True
    )
    yield
    await dispose_engine()


@pytest.fixture(scope="session")
async def app(database: None):  # type: ignore[no-untyped-def]
    from app.core.modules.registry import get_registry
    from app.main import create_app, startup

    application = create_app()
    await startup(get_registry())
    return application


class ApiClient(httpx.AsyncClient):
    csrf: str = ""

    async def login(self, upn: str) -> ApiClient:
        resp = await self.post("/api/core/auth/login", json={"username": upn})
        assert resp.status_code == 204, resp.text
        me = await self.get("/api/core/me")
        self.csrf = me.json()["csrf_token"]
        self.headers["X-CSRF-Token"] = self.csrf
        return self


@pytest.fixture
async def client(app) -> AsyncIterator[ApiClient]:  # type: ignore[no-untyped-def]
    async with ApiClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin(app) -> AsyncIterator[ApiClient]:  # type: ignore[no-untyped-def]
    async with ApiClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield await c.login("admin@test.local")


@pytest.fixture
def settings():  # type: ignore[no-untyped-def]
    return get_settings()
