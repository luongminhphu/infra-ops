import json

from sqlalchemy import select

from app.core.audit.models import AuditLog
from app.core.connections.models import ConnectionProfile
from app.core.connections.service import build_context
from app.core.connectors.base import Purpose
from app.core.db import get_sessionmaker

SECRET = "sdp-authtoken-ABCDEF123456"


async def _create(admin, name: str = "SDP Prod") -> dict:  # type: ignore[no-untyped-def]
    resp = await admin.post(
        "/api/core/connections",
        json={
            "system_type": "sdp",
            "name": name,
            "base_url": "https://sdp.example.local:8080/",
            "config": {"edition": "onprem", "verify_tls": False},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def test_connector_catalog(admin) -> None:  # type: ignore[no-untyped-def]
    types = {c["system_type"]: c for c in (await admin.get("/api/core/connectors")).json()}
    assert set(types) >= {"sdp", "biostar"}
    assert "edition" in types["sdp"]["config_schema"]["properties"]
    # The Cloud data center is only shown for the Cloud edition (on-prem users never see it).
    assert types["sdp"]["config_schema"]["properties"]["data_center"]["x-show-when"] == {"edition": "cloud"}
    assert [a["key"] for a in types["biostar"]["auth_types"]] == ["username_password"]


async def test_create_validates_config(admin) -> None:  # type: ignore[no-untyped-def]
    resp = await admin.post(
        "/api/core/connections",
        json={"system_type": "sdp", "name": "Bad", "base_url": "https://x", "config": {"edition": "mars"}},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_config"


async def test_secret_never_returned_and_encrypted(admin) -> None:  # type: ignore[no-untyped-def]
    profile = await _create(admin, "SDP Secret")
    assert profile["base_url"] == "https://sdp.example.local:8080"
    assert profile["status"] == "incomplete"
    assert {"id": "lifecycle", "name": "Vòng đời nhân sự"} in profile["used_by"]
    assert {"id": "workload", "name": "Công việc Infra Support"} in profile["used_by"]
    resp = await admin.put(
        f"/api/core/connections/{profile['id']}/credentials/read",
        json={"auth_type": "api_key", "secrets": {"api_key": SECRET}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert SECRET not in body
    cred = resp.json()["credentials"]["read"]
    assert cred["hint"] == SECRET[-4:]
    assert resp.json()["credentials"]["write"] is None
    assert resp.json()["status"] == "untested"

    for path in (
        "/api/core/connections",
        f"/api/core/connections/{profile['id']}",
        "/api/core/connections/export",
        "/api/core/audit?limit=200",
    ):
        assert SECRET not in (await admin.get(path)).text

    async with get_sessionmaker()() as s:
        row = await s.get(ConnectionProfile, profile["id"])
        assert row is not None
        assert SECRET.encode() not in row.credentials[0].ciphertext
        ctx = build_context(row, Purpose.READ, 5)
        assert ctx.credential("api_key") == SECRET


async def test_empty_secret_keeps_existing_and_rotation_is_audited(admin) -> None:  # type: ignore[no-untyped-def]
    profile = await _create(admin, "SDP Rotate")
    url = f"/api/core/connections/{profile['id']}/credentials/write"
    await admin.put(url, json={"auth_type": "api_key", "secrets": {"api_key": SECRET}})
    resp = await admin.put(url, json={"auth_type": "api_key", "secrets": {"api_key": ""}})
    assert resp.status_code == 200
    async with get_sessionmaker()() as s:
        row = await s.get(ConnectionProfile, profile["id"])
        assert row is not None
        assert build_context(row, Purpose.WRITE, 5).credential("api_key") == SECRET
    await admin.put(url, json={"auth_type": "api_key", "secrets": {"api_key": "new-token-0000000099"}})
    async with get_sessionmaker()() as s:
        actions = (await s.scalars(select(AuditLog.action).where(AuditLog.target_id == profile["id"]))).all()
    assert "connection.credential_set" in actions
    assert "connection.credential_rotated" in actions


async def test_missing_required_secret(admin) -> None:  # type: ignore[no-untyped-def]
    profile = await _create(admin, "SDP Missing")
    resp = await admin.put(
        f"/api/core/connections/{profile['id']}/credentials/read",
        json={"auth_type": "api_key", "secrets": {}},
    )
    assert resp.json()["error"]["code"] == "credential_incomplete"


async def test_auth_type_must_match_connector(admin) -> None:  # type: ignore[no-untyped-def]
    profile = await _create(admin, "SDP AuthType")
    resp = await admin.put(
        f"/api/core/connections/{profile['id']}/credentials/read",
        json={"auth_type": "certificate", "secrets": {"certificate_pem": "x"}},
    )
    assert resp.json()["error"]["code"] == "invalid_auth_type"


async def test_optimistic_locking(admin) -> None:  # type: ignore[no-untyped-def]
    profile = await _create(admin, "SDP Lock")
    ok = await admin.patch(f"/api/core/connections/{profile['id']}", json={"version": 1, "notes": "a"})
    assert ok.json()["version"] == 2
    stale = await admin.patch(f"/api/core/connections/{profile['id']}", json={"version": 1, "notes": "b"})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"


async def test_write_capability_unavailable_without_write_credential(admin) -> None:  # type: ignore[no-untyped-def]
    import pytest

    from app.core.connections.service import open_connection
    from app.core.errors import CapabilityUnavailable

    profile = await _create(admin, "SDP ReadOnly")
    await admin.put(
        f"/api/core/connections/{profile['id']}/credentials/read",
        json={"auth_type": "api_key", "secrets": {"api_key": SECRET}},
    )
    async with get_sessionmaker()() as s:
        _, ctx = await open_connection(s, "sdp", Purpose.READ, profile_id=profile["id"])
        assert ctx.purpose == Purpose.READ
        with pytest.raises(CapabilityUnavailable) as exc:
            await open_connection(s, "sdp", Purpose.WRITE, profile_id=profile["id"])
    assert "Write" in exc.value.message


async def test_export_import_roundtrip_without_secrets(admin) -> None:  # type: ignore[no-untyped-def]
    profile = await _create(admin, "SDP Export")
    await admin.put(
        f"/api/core/connections/{profile['id']}/credentials/read",
        json={"auth_type": "api_key", "secrets": {"api_key": SECRET}},
    )
    exported = (await admin.get("/api/core/connections/export")).json()
    assert SECRET not in json.dumps(exported)
    item = next(c for c in exported["connections"] if c["name"] == "SDP Export")
    item["name"] = "SDP Imported"
    item["notes"] = "from export"
    result = (
        await admin.post(
            "/api/core/connections/import", json={"format": exported["format"], "connections": [item]}
        )
    ).json()
    assert result == {"created": ["SDP Imported"], "updated": [], "errors": [], "credentials_cleared": []}
    again = (
        await admin.post(
            "/api/core/connections/import", json={"format": exported["format"], "connections": [item]}
        )
    ).json()
    assert again["updated"] == ["SDP Imported"]


async def test_test_without_credential_reports_failure(admin) -> None:  # type: ignore[no-untyped-def]
    profile = await _create(admin, "SDP NoCred")
    result = (await admin.post(f"/api/core/connections/{profile['id']}/test?purpose=write")).json()
    assert result["ok"] is False
    assert result["steps"][0]["key"] == "credential"


async def test_audit_chain_valid_and_immutable(admin) -> None:  # type: ignore[no-untyped-def]
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    await _create(admin, "SDP Audit")
    verify = (await admin.get("/api/core/audit/verify")).json()
    assert verify["ok"] is True and verify["checked"] > 0
    async with get_sessionmaker()() as s:
        with pytest.raises(DBAPIError, match="append-only"):
            await s.execute(text("UPDATE core_audit_log SET action='x'"))
        await s.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            await s.execute(text("DELETE FROM core_audit_log"))
        await s.rollback()


async def test_audit_filters_and_pagination(admin) -> None:  # type: ignore[no-untyped-def]
    page = (await admin.get("/api/core/audit?module=core&limit=2")).json()
    assert len(page["items"]) == 2
    assert page["next_before_id"] is not None
    nxt = (await admin.get(f"/api/core/audit?module=core&limit=2&before_id={page['next_before_id']}")).json()
    assert nxt["items"][0]["id"] < page["items"][-1]["id"]
    facets = (await admin.get("/api/core/audit/facets")).json()
    assert "connection.created" in facets["actions"]


async def test_dashboard_widgets(admin) -> None:  # type: ignore[no-untyped-def]
    widgets = (await admin.get("/api/core/dashboard")).json()
    keys = [w["key"] for w in widgets]
    assert {"core.connections", "core.expiring_secrets", "lifecycle.reconciliation"} <= set(keys)
    assert all(w["error"] is None for w in widgets)


async def test_changing_host_deletes_credentials(admin) -> None:  # type: ignore[no-untyped-def]
    # Stored secrets must never be sent to a new host (e.g. via "Kiểm tra kết nối" after an edit).
    profile = await _create(admin, "SDP Move")
    url = f"/api/core/connections/{profile['id']}"
    put = await admin.put(
        f"{url}/credentials/read", json={"auth_type": "api_key", "secrets": {"api_key": SECRET}}
    )
    same_host = await admin.patch(
        url, json={"version": put.json()["version"], "base_url": "https://sdp.example.local:8080/api/"}
    )
    assert same_host.json()["credentials_cleared"] is False
    assert same_host.json()["credentials"]["read"] is not None
    moved = await admin.patch(
        url, json={"version": same_host.json()["version"], "base_url": "https://evil.example.com:8080"}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["credentials_cleared"] is True
    assert moved.json()["credentials"] == {"read": None, "write": None}
    async with get_sessionmaker()() as s:
        entry = await s.scalar(
            select(AuditLog).where(
                AuditLog.action == "connection.credentials_cleared", AuditLog.target_id == profile["id"]
            )
        )
        assert entry is not None

    # Import that moves an existing profile to another host clears its credentials too.
    await admin.put(f"{url}/credentials/read", json={"auth_type": "api_key", "secrets": {"api_key": SECRET}})
    exported = (await admin.get("/api/core/connections/export")).json()
    item = next(c for c in exported["connections"] if c["name"] == "SDP Move")
    item["base_url"] = "https://other.example.com"
    result = (
        await admin.post(
            "/api/core/connections/import", json={"format": exported["format"], "connections": [item]}
        )
    ).json()
    assert result["credentials_cleared"] == ["SDP Move"]
    assert (await admin.get(url)).json()["credentials"]["read"] is None
