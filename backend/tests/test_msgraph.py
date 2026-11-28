"""Microsoft Graph connector against mocked HTTP. No real system is contacted."""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.core.connectors import msgraph as mod
from app.core.connectors.base import ConnectionContext, Purpose, TestReport, TestStep
from app.core.connectors.msgraph import GraphClient, MsGraphConfig, MsGraphConnector

G = "https://graph.microsoft.com/v1.0"
L = "https://login.microsoftonline.com"
TENANT = "11111111-2222-3333-4444-555555555555"
SECRET = "s3cret-value-xyz"
TOKEN_URL = f"{L}/{TENANT}/oauth2/v2.0/token"
UID = "aaaaaaaa-1111-2222-3333-444444444444"
UID2 = "bbbbbbbb-1111-2222-3333-444444444444"


def jwt(roles: list[str]) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"roles": roles}).encode()).decode().rstrip("=")
    return f"h.{body}.sig"


def ctx() -> ConnectionContext:
    return ConnectionContext(
        G,
        MsGraphConfig(tenant_id=TENANT),
        Purpose.WRITE,
        "oauth2_client_credentials",
        {"client_id": "cid"},
        {"client_secret": SECRET},
        5,
    )


@pytest.fixture(autouse=True)
def _skip_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(report: TestReport, url: str, *, verify: bool, connect_timeout: float) -> bool:
        report.add(TestStep("dns", "DNS", "ok"))
        return True

    monkeypatch.setattr(mod, "check_reachability", fake)


def token_route(roles: list[str] | None = None):  # type: ignore[no-untyped-def]
    return respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": jwt(roles or [])})
    )


@respx.mock
async def test_token_flow_cached_and_find_user() -> None:
    tok = token_route()
    route = respx.get(f"{G}/users").mock(return_value=httpx.Response(200, json={"value": [{"id": "u1"}]}))
    async with GraphClient(ctx()) as c:
        assert await c.find_user_by_employee_id("003551") == [{"id": "u1"}]
        await c.find_user_by_employee_id("003551")
    assert tok.call_count == 1
    form = parse_qs(tok.calls[0].request.content.decode())
    assert form["grant_type"] == ["client_credentials"] and form["scope"] == [mod.SCOPE]
    req = route.calls[0].request
    assert req.headers["ConsistencyLevel"] == "eventual"
    assert req.headers["Authorization"].startswith("Bearer ")
    assert req.url.params["$filter"] == "employeeId eq '003551'"
    assert req.url.params["$count"] == "true"


@respx.mock
async def test_find_user_by_upn_escapes_quotes() -> None:
    token_route()
    route = respx.get(f"{G}/users").mock(return_value=httpx.Response(200, json={"value": [{"id": "u1"}]}))
    async with GraphClient(ctx()) as c:
        assert await c.find_user_by_upn("o'brien@corp.vn") == {"id": "u1"}
    req = route.calls[0].request
    assert req.url.params["$filter"] == "userPrincipalName eq 'o''brien@corp.vn'"
    assert req.headers["ConsistencyLevel"] == "eventual"
    route.mock(return_value=httpx.Response(200, json={"value": []}))
    async with GraphClient(ctx()) as c:
        assert await c.find_user_by_upn("x@corp.vn") is None


async def test_invalid_employee_id_raises() -> None:
    async with GraphClient(ctx()) as c:
        for bad in ("1' or 1 eq 1", "", "12a", "1" * 21):
            with pytest.raises(ValueError):
                await c.find_user_by_employee_id(bad)


@respx.mock
async def test_write_calls_and_helpers() -> None:
    token_route()
    patch = respx.patch(f"{G}/users/{UID}").mock(return_value=httpx.Response(204))
    assign = respx.post(f"{G}/users/{UID}/assignLicense").mock(return_value=httpx.Response(200, json={}))
    revoke = respx.post(f"{G}/users/{UID}/revokeSignInSessions").mock(
        return_value=httpx.Response(200, json={"value": True})
    )
    respx.get(f"{G}/directory/deletedItems/{UID2}").mock(return_value=httpx.Response(404, json={}))
    respx.get(f"{G}/subscribedSkus").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "skuId": "SKU-A",
                        "skuPartNumber": "E3",
                        "prepaidUnits": {"enabled": 10},
                        "consumedUnits": 4,
                    }
                ]
            },
        )
    )
    async with GraphClient(ctx()) as c:
        await c.set_usage_location(UID, "VN")
        await c.assign_licenses(UID, ["SKU-A"])
        await c.revoke_sessions(UID)
        assert await c.deleted_user(UID2) is None
        assert await c.free_units("sku-a") == 6
        assert await c.free_units("other") is None
    assert json.loads(patch.calls[0].request.content) == {"usageLocation": "VN"}
    assert json.loads(assign.calls[0].request.content)["addLicenses"][0]["skuId"] == "SKU-A"
    assert revoke.call_count == 1


@respx.mock
async def test_errors_hide_secrets() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(401, json={"error": "invalid_client", "error_description": SECRET})
    )
    async with GraphClient(ctx()) as c:
        with pytest.raises(mod.MsGraphError) as ei:
            await c.token()
    assert SECRET not in str(ei.value) and SECRET not in ei.value.hint
    assert "invalid_client" in str(ei.value)


@respx.mock
async def test_graph_error_has_hint() -> None:
    token_route()
    respx.get(f"{G}/users/{UID}").mock(
        return_value=httpx.Response(403, json={"error": {"code": "Authorization_RequestDenied"}})
    )
    async with GraphClient(ctx()) as c:
        with pytest.raises(mod.MsGraphError) as ei:
            await c.get_user(UID)
    assert ei.value.status == 403 and ei.value.hint


@respx.mock
async def test_connection_read_only_and_roles() -> None:
    tok = token_route(["User.ReadWrite.All"])
    respx.get(f"{G}/subscribedSkus").mock(return_value=httpx.Response(200, json={"value": []}))
    respx.get(f"{G}/users").mock(return_value=httpx.Response(200, json={"value": []}))
    report = await MsGraphConnector().test_connection(ctx())
    assert report.ok
    assert [c.request.method for c in respx.calls] == ["POST", "GET", "GET"]
    assert tok.call_count == 1 and str(respx.calls[0].request.url) == TOKEN_URL
    assert [p.granted for p in report.permissions] == [True, True, True, True]
    assert SECRET not in json.dumps(report.to_dict())


@respx.mock
async def test_connection_unknown_and_missing_roles() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "opaque"}))
    respx.get(f"{G}/subscribedSkus").mock(return_value=httpx.Response(403, json={"error": {"code": "x"}}))
    respx.get(f"{G}/users").mock(return_value=httpx.Response(200, json={"value": []}))
    report = await MsGraphConnector().test_connection(ctx())
    assert [p.granted for p in report.permissions] == [False, True, None, None]
    assert mod.token_roles(jwt(["A"])) == ["A"] and mod.token_roles("opaque") is None


def cfg_ctx(base: str = G, authority: str = L) -> ConnectionContext:
    cfg = MsGraphConfig.model_construct(tenant_id=TENANT, authority_host=authority, timeout_seconds=5.0)
    return ConnectionContext(
        base,
        cfg,
        Purpose.WRITE,
        "oauth2_client_credentials",
        {"client_id": "cid"},
        {"client_secret": SECRET},
        5,
    )


def test_refuses_non_https() -> None:
    for kw in ({"base": "http://graph.local/v1.0"}, {"authority": "http://login.local"}):
        with pytest.raises(mod.MsGraphError):
            GraphClient(cfg_ctx(**kw))


@respx.mock
async def test_token_refreshed_after_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    tok = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
    )
    async with GraphClient(ctx()) as c:
        await c.token()
        now[0] += 3500
        await c.token()
        assert tok.call_count == 1
        now[0] += 100  # 3600 - 60 s margin passed
        await c.token()
    assert tok.call_count == 2


@respx.mock
async def test_401_refreshes_token_and_retries_write_once() -> None:
    tok = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "old", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "new", "expires_in": 3600}),
        ]
    )
    patch = respx.patch(f"{G}/users/{UID}").mock(
        side_effect=[
            httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}}),
            httpx.Response(204),
        ]
    )
    async with GraphClient(ctx()) as c:
        await c.set_usage_location(UID, "VN")
    assert tok.call_count == 2 and patch.call_count == 2
    assert patch.calls[1].request.headers["Authorization"] == "Bearer new"


@respx.mock
async def test_persistent_401_raises_after_one_retry() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
    )
    patch = respx.patch(f"{G}/users/{UID}").mock(return_value=httpx.Response(401, json={}))
    async with GraphClient(ctx()) as c:
        with pytest.raises(mod.MsGraphError):
            await c.set_usage_location(UID, "VN")
    assert patch.call_count == 2


async def test_user_id_must_be_guid() -> None:
    async with GraphClient(ctx()) as c:
        for bad in ("u1", "../me", f"{UID}/x"):
            with pytest.raises(ValueError):
                await c.get_user(bad)


@respx.mock
async def test_address_taken_checks_live_and_deleted_users() -> None:
    token_route()
    live = respx.get(f"{G}/users").mock(return_value=httpx.Response(200, json={"value": []}))
    gone = respx.get(f"{G}/directory/deletedItems/microsoft.graph.user").mock(
        return_value=httpx.Response(200, json={"value": [{"id": "x"}]})
    )
    async with GraphClient(ctx()) as c:
        assert await c.address_taken("o'neil@corp.vn") is True
    req = live.calls[0].request
    flt = req.url.params["$filter"]
    assert "userPrincipalName eq 'o''neil@corp.vn'" in flt and "mail eq 'o''neil@corp.vn'" in flt
    assert "proxyAddresses/any(p:p eq 'smtp:o''neil@corp.vn')" in flt
    assert req.headers["ConsistencyLevel"] == "eventual" and req.url.params["$count"] == "true"
    assert "proxyAddresses" not in gone.calls[0].request.url.params["$filter"]


@respx.mock
async def test_address_taken_live_hit_skips_deleted_and_false_when_free() -> None:
    token_route()
    users = respx.get(f"{G}/users").mock(
        side_effect=[
            httpx.Response(200, json={"value": [{"id": "u"}]}),
            httpx.Response(200, json={"value": []}),
        ]
    )
    gone = respx.get(f"{G}/directory/deletedItems/microsoft.graph.user").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    async with GraphClient(ctx()) as c:
        assert await c.address_taken("a@b.vn") is True
        assert gone.call_count == 0
        assert await c.address_taken("a@b.vn") is False
    assert users.call_count == 2 and gone.call_count == 1


@respx.mock
async def test_swap_licenses_single_call() -> None:
    token_route()
    assign = respx.post(f"{G}/users/{UID}/assignLicense").mock(return_value=httpx.Response(200, json={}))
    a, b = UID, UID2
    async with GraphClient(ctx()) as c:
        await c.swap_licenses(UID, [], [])
        assert assign.call_count == 0
        await c.swap_licenses(UID, [a], [b])
        with pytest.raises(ValueError):
            await c.swap_licenses(UID, ["bad"], [])
    assert assign.call_count == 1
    body = json.loads(assign.calls[0].request.content)
    assert body == {"addLicenses": [{"skuId": a, "disabledPlans": []}], "removeLicenses": [b]}
