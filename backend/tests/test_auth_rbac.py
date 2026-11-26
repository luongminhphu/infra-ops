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
    assert r.json() == {"mode": "dev", "cookie_secure": False, "local_login": False}


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


async def test_system_roles_listed_least_to_most_privileged(admin) -> None:  # type: ignore[no-untyped-def]
    roles = [r for r in (await admin.get("/api/core/roles")).json() if r["is_system"]]
    assert [r["code"] for r in roles] == ["viewer", "operator", "supervisor", "manager", "admin"]
    assert [r["name"] for r in roles] == ["Viewer", "Operator", "Supervisor", "Manager", "Administrator"]


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


async def test_sso_with_local_accounts(client, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # SSO by default; local accounts only when INFRAOPS_ALLOW_LOCAL_LOGIN is on, and only those with a password.
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    async with get_sessionmaker()() as s:
        s.add(
            User(
                upn="break-glass@test.local",
                display_name="BG",
                password_hash=hash_password("Good-Password-1"),
            )
        )
        s.add(User(upn="sso-only@test.local", display_name="SSO"))
        await s.commit()
    creds = {"username": "break-glass@test.local", "password": "Good-Password-1"}
    off = await client.post("/api/core/auth/login", json=creds)
    assert off.json()["error"]["code"] == "use_sso"
    monkeypatch.setattr(settings, "allow_local_login", True)
    cfg = (await client.get("/api/core/auth/config")).json()
    assert cfg["mode"] == "oidc" and cfg["local_login"] is True
    sso_only = await client.post(
        "/api/core/auth/login", json={"username": "sso-only@test.local", "password": "x"}
    )
    assert sso_only.status_code == 401
    assert (await client.post("/api/core/auth/login", json=creds)).status_code == 204
    assert (await client.get("/api/core/me")).json()["local_login"] is True


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


def test_client_ip_is_clipped_to_column_size() -> None:
    from starlette.requests import Request

    from app.core.auth.deps import client_ip

    assert client_ip(Request({"type": "http", "client": ("1" * 300, 1)})) == "1" * 64


async def _local_user(upn: str, password: str) -> None:
    async with get_sessionmaker()() as s:
        s.add(User(upn=upn, display_name=upn, password_hash=hash_password(password)))
        await s.commit()


async def test_parallel_failed_logins_all_counted(app, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    import httpx

    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "local_max_failed_logins", 4)
    await _local_user("parallel@test.local", "Good-Password-1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        bad = {"username": "PARALLEL@test.local", "password": "x"}
        results = await asyncio.gather(*(c.post("/api/core/auth/login", json=bad) for _ in range(4)))
    assert all(r.status_code == 401 for r in results)
    async with get_sessionmaker()() as s:
        user = await s.scalar(select(User).where(User.upn == "parallel@test.local"))
        assert user is not None and user.locked_until is not None  # no lost increments


async def test_password_change_signs_out_other_sessions(app, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import httpx

    monkeypatch.setattr(settings, "auth_mode", "local")
    await _local_user("rotate@test.local", "Good-Password-1")
    creds = {"username": "rotate@test.local", "password": "Good-Password-1"}

    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async with make() as a, make() as b:
        for c in (a, b):
            assert (await c.post("/api/core/auth/login", json=creds)).status_code == 204
        a.headers["X-CSRF-Token"] = (await a.get("/api/core/me")).json()["csrf_token"]
        resp = await a.post(
            "/api/core/me/password",
            json={"current_password": "Good-Password-1", "new_password": "New-Password-22"},
        )
        assert resp.status_code == 204, resp.text
        assert (await a.get("/api/core/me")).status_code == 200
        assert (await b.get("/api/core/me")).status_code == 401


# --- SSO callback (Entra ID mocked: token endpoint via respx, JWKS from a local RSA key) ---


def _oidc_setup(settings, monkeypatch):  # type: ignore[no-untyped-def]
    import json

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.core.auth import oidc

    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_tenant_id", "tenant-1")
    monkeypatch.setattr(settings, "oidc_client_id", "client-1")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="k1", use="sig", alg="RS256")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": [jwk]})
    monkeypatch.setattr(oidc, "_jwks_clients", {})  # drop keys cached by earlier tests
    return key


async def _sso_login(client, settings, key, oid: str, upn: str):  # type: ignore[no-untyped-def]
    import time
    from urllib.parse import parse_qs, urlsplit

    import httpx
    import jwt
    import respx

    start = await client.get("/api/core/auth/oidc/login", params={"next": "/me/work"})
    assert start.status_code == 302
    q = parse_qs(urlsplit(start.headers["location"]).query)
    now = int(time.time())
    claims = {
        "iss": settings.oidc_issuer,
        "aud": "client-1",
        "iat": now,
        "exp": now + 300,
        "nonce": q["nonce"][0],
        "oid": oid,
        "preferred_username": upn,
        "name": "SSO User",
    }
    id_token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})
    token_url = f"{settings.oidc_authority_host}/tenant-1/oauth2/v2.0/token"
    with respx.mock(assert_all_called=True) as mock:
        mock.post(token_url).mock(return_value=httpx.Response(200, json={"id_token": id_token}))
        return await client.get("/api/core/auth/oidc/callback", params={"code": "c1", "state": q["state"][0]})


async def test_sso_callback_bad_state_redirects(client, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _oidc_setup(settings, monkeypatch)
    resp = await client.get("/api/core/auth/oidc/callback", params={"code": "c1", "state": "forged"})
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?error=sso"


async def test_sso_callback_first_login_creates_viewer(client, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    key = _oidc_setup(settings, monkeypatch)
    resp = await _sso_login(client, settings, key, "oid-cb-1", "sso.cb@test.local")
    assert resp.status_code == 302, resp.text
    assert resp.headers["location"] == "/me/work"
    me = (await client.get("/api/core/me")).json()
    assert me["upn"] == "sso.cb@test.local" and me["roles"] == ["viewer"]


async def test_sso_recycled_upn_does_not_take_over_account(client, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.audit.models import AuditLog

    key = _oidc_setup(settings, monkeypatch)
    async with get_sessionmaker()() as s:
        s.add(User(upn="recycled@test.local", display_name="Old Person", entra_oid="oid-old"))
        await s.commit()
    resp = await _sso_login(client, settings, key, "oid-new", "recycled@test.local")
    assert resp.headers["location"] == "/login?error=sso"
    assert (await client.get("/api/core/me")).status_code == 401
    async with get_sessionmaker()() as s:
        user = await s.scalar(select(User).where(User.upn == "recycled@test.local"))
        assert user is not None and user.entra_oid == "oid-old"
        denied = await s.scalar(
            select(AuditLog).where(AuditLog.actor_upn == "recycled@test.local", AuditLog.result == "denied")
        )
        assert denied is not None


async def test_admin_deletes_an_account(admin, client) -> None:  # type: ignore[no-untyped-def]
    me = (await admin.get("/api/core/me")).json()
    assert (await admin.delete(f"/api/core/users/{me['id']}")).json()["error"]["code"] == "self_delete"
    made = await admin.post(
        "/api/core/users", json={"upn": "to-delete@test.local", "display_name": "X", "role_ids": []}
    )
    assert made.status_code == 201, made.text
    uid = made.json()["id"]
    await client.login("to-delete@test.local")
    assert (await client.delete(f"/api/core/users/{uid}")).status_code == 403  # needs user management
    assert (await admin.delete(f"/api/core/users/{uid}")).status_code == 204
    assert "to-delete@test.local" not in {u["upn"] for u in (await admin.get("/api/core/users")).json()}
    logs = (await admin.get("/api/core/audit", params={"action": "rbac.user_deleted"})).json()
    assert any(e["target_label"] == "to-delete@test.local" for e in logs["items"])
