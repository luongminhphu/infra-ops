from sqlalchemy import select

from app.core.auth.models import User
from app.core.auth.passwords import hash_password
from app.core.db import get_sessionmaker


async def test_me_requires_login(client) -> None:  # type: ignore[no-untyped-def]
    resp = await client.get("/api/core/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


async def test_auth_config_reports_secure_cookie(client) -> None:  # type: ignore[no-untyped-def]
    # The login page warns on plain http:// when the session cookie is Secure (browsers would drop it).
    r = await client.get("/api/core/auth/config")
    assert r.json() == {"mode": "dev", "cookie_secure": False}


async def test_bootstrap_admin_gets_all_permissions(admin) -> None:  # type: ignore[no-untyped-def]
    me = (await admin.get("/api/core/me")).json()
    assert "admin" in me["roles"]
    assert "core.connections.manage" in me["permissions"]
    assert "lifecycle.job.execute" in me["permissions"]
    labels = [g["module"] for g in me["nav"]]
    assert "core" in labels


async def test_new_user_has_no_permissions(client) -> None:  # type: ignore[no-untyped-def]
    await client.login("nobody@test.local")
    me = (await client.get("/api/core/me")).json()
    assert me["permissions"] == []
    assert (await client.get("/api/core/connections")).status_code == 403


async def test_csrf_required_for_mutations(admin) -> None:  # type: ignore[no-untyped-def]
    del admin.headers["X-CSRF-Token"]
    resp = await admin.post("/api/core/connections", json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"


async def test_logout_invalidates_session(client) -> None:  # type: ignore[no-untyped-def]
    await client.login("logout@test.local")
    assert (await client.post("/api/core/auth/logout")).status_code == 204
    assert (await client.get("/api/core/me")).status_code == 401


async def test_operator_defaults(admin, client) -> None:  # type: ignore[no-untyped-def]
    roles = {r["code"]: r for r in (await admin.get("/api/core/roles")).json()}
    hd = roles["operator"]
    assert "lifecycle.job.execute" in hd["permissions"]
    assert "core.connections.manage" not in hd["permissions"]
    created = await admin.post(
        "/api/core/users",
        json={"upn": "hd1@test.local", "display_name": "HD One", "role_ids": [hd["id"]]},
    )
    assert created.status_code == 201, created.text
    await client.login("hd1@test.local")
    assert (await client.get("/api/core/connections")).status_code == 200
    resp = await client.post(
        "/api/core/connections", json={"system_type": "sdp", "name": "x", "base_url": "https://x"}
    )
    assert resp.status_code == 403


async def test_revoked_default_permission_is_not_regranted(admin, app) -> None:  # type: ignore[no-untyped-def]
    from app.core.modules.registry import get_registry
    from app.main import startup

    roles = {r["code"]: r for r in (await admin.get("/api/core/roles")).json()}
    viewer = roles["viewer"]
    perms = [p for p in viewer["permissions"] if p != "lifecycle.job.view"]
    resp = await admin.patch(f"/api/core/roles/{viewer['id']}", json={"permissions": perms})
    assert resp.status_code == 200
    await startup(get_registry())  # simulates a restart
    roles = {r["code"]: r for r in (await admin.get("/api/core/roles")).json()}
    assert "lifecycle.job.view" not in roles["viewer"]["permissions"]


async def test_admin_role_permissions_locked(admin) -> None:  # type: ignore[no-untyped-def]
    roles = {r["code"]: r for r in (await admin.get("/api/core/roles")).json()}
    resp = await admin.patch(f"/api/core/roles/{roles['admin']['id']}", json={"permissions": []})
    assert resp.json()["error"]["code"] == "admin_role_locked"


async def test_cannot_remove_last_admin(admin) -> None:  # type: ignore[no-untyped-def]
    me = (await admin.get("/api/core/me")).json()
    resp = await admin.patch(f"/api/core/users/{me['id']}", json={"role_ids": []})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "last_admin"


async def test_unknown_permission_rejected(admin) -> None:  # type: ignore[no-untyped-def]
    resp = await admin.post(
        "/api/core/roles", json={"code": "custom", "name": "C", "permissions": ["nope.x.y"]}
    )
    assert resp.json()["error"]["code"] == "unknown_permission"


async def test_local_login_and_lockout(client, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "auth_mode", "local")
    async with get_sessionmaker()() as s:
        s.add(
            User(upn="local@test.local", display_name="Local", password_hash=hash_password("Good-Password-1"))
        )
        await s.commit()
    bad = await client.post("/api/core/auth/login", json={"username": "local@test.local", "password": "x"})
    assert bad.status_code == 401
    unknown = await client.post(
        "/api/core/auth/login", json={"username": "ghost@test.local", "password": "x"}
    )
    assert unknown.json()["error"]["message"] == bad.json()["error"]["message"]  # no user enumeration
    for _ in range(settings.local_max_failed_logins):
        await client.post("/api/core/auth/login", json={"username": "local@test.local", "password": "x"})
    locked = await client.post(
        "/api/core/auth/login", json={"username": "local@test.local", "password": "Good-Password-1"}
    )
    assert locked.status_code == 401
    assert "tạm khoá" in locked.json()["error"]["message"]
    async with get_sessionmaker()() as s:
        user = await s.scalar(select(User).where(User.upn == "local@test.local"))
        assert user is not None
        user.locked_until = None
        await s.commit()
    ok = await client.post(
        "/api/core/auth/login", json={"username": "local@test.local", "password": "Good-Password-1"}
    )
    assert ok.status_code == 204
