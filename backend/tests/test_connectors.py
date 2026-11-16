"""Connector tests against mocked HTTP servers. No real system is contacted."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.connectors import biostar as biostar_mod
from app.core.connectors import sdp as sdp_mod
from app.core.connectors.base import ConnectionContext, Purpose, TestReport, TestStep
from app.core.connectors.biostar import BioStarConfig, BioStarConnector
from app.core.connectors.sdp import SdpConfig, SdpConnector

SDP = "https://sdp.test.local:8080"
BS = "https://biostar.test.local"


@pytest.fixture(autouse=True)
def _skip_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(report: TestReport, url: str, *, verify: bool, connect_timeout: float) -> bool:
        report.add(TestStep("dns", "DNS", "ok"))
        report.add(TestStep("tls", "TLS", "ok"))
        return True

    monkeypatch.setattr(sdp_mod, "check_reachability", fake)
    monkeypatch.setattr(biostar_mod, "check_reachability", fake)


def sdp_ctx(purpose: Purpose = Purpose.READ, **secrets: str) -> ConnectionContext:
    return ConnectionContext(SDP, SdpConfig(), purpose, "api_key", {}, secrets or {"api_key": "k"}, 5)


def ok_list(total: int) -> dict:
    return {
        "response_status": [{"status_code": 2000, "status": "success"}],
        "list_info": {"total_count": total},
    }


@respx.mock
async def test_sdp_success_lists_permissions() -> None:
    req = respx.get(f"{SDP}/api/v3/requests").mock(return_value=httpx.Response(200, json=ok_list(42)))
    respx.get(f"{SDP}/api/v3/assets").mock(return_value=httpx.Response(200, json=ok_list(7)))
    respx.get(f"{SDP}/api/v3/users").mock(return_value=httpx.Response(200, json=ok_list(3)))
    report = await SdpConnector().test_connection(sdp_ctx())
    assert report.ok
    assert req.calls[0].request.headers["authtoken"] == "k"
    assert "vnd.manageengine.sdp.v3+json" in req.calls[0].request.headers["accept"]
    assert [p.granted for p in report.permissions] == [True, True, True]
    assert "42 request" in report.steps[2].detail


@respx.mock
async def test_sdp_invalid_key_explains_fix() -> None:
    respx.get(f"{SDP}/api/v3/requests").mock(
        return_value=httpx.Response(
            401,
            json={
                "response_status": {
                    "status_code": 4000,
                    "status": "failed",
                    "messages": [{"status_code": 4002, "type": "failed", "message": "UNAUTHORISED"}],
                }
            },
        )
    )
    report = await SdpConnector().test_connection(sdp_ctx())
    assert not report.ok
    auth = report.steps[-1]
    assert auth.key == "auth" and auth.status == "fail"
    assert "UNAUTHORISED" in auth.detail
    assert "API key" in auth.hint


@respx.mock
async def test_sdp_partial_permissions_warn_and_write_unverifiable() -> None:
    respx.get(f"{SDP}/api/v3/requests").mock(return_value=httpx.Response(200, json=ok_list(1)))
    respx.get(f"{SDP}/api/v3/assets").mock(
        return_value=httpx.Response(
            403, json={"response_status": {"status": "failed", "messages": [{"message": "Not permitted"}]}}
        )
    )
    respx.get(f"{SDP}/api/v3/users").mock(return_value=httpx.Response(200, json=ok_list(1)))
    report = await SdpConnector().test_connection(sdp_ctx(Purpose.WRITE))
    assert report.ok  # warnings do not fail the test
    assert report.steps[-1].status == "warn"
    assert report.permissions[1].granted is False
    assert report.permissions[-1].granted is None


@respx.mock
async def test_sdp_non_json_points_to_base_url() -> None:
    respx.get(f"{SDP}/api/v3/requests").mock(return_value=httpx.Response(200, text="<html>login</html>"))
    report = await SdpConnector().test_connection(sdp_ctx())
    assert not report.ok
    assert "Base URL" in report.steps[-1].hint


@respx.mock
async def test_sdp_retries_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.core.connectors.http.asyncio.sleep", no_sleep)
    route = respx.get(f"{SDP}/api/v3/requests").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json=ok_list(1)),
            httpx.Response(200, json=ok_list(1)),  # permission probe
        ]
    )
    respx.get(f"{SDP}/api/v3/assets").mock(return_value=httpx.Response(200, json=ok_list(1)))
    respx.get(f"{SDP}/api/v3/users").mock(return_value=httpx.Response(200, json=ok_list(1)))
    report = await SdpConnector().test_connection(sdp_ctx())
    assert report.ok
    assert route.call_count == 3


@respx.mock
async def test_sdp_cloud_refreshes_zoho_token() -> None:
    token = respx.post("https://accounts.zoho.com/oauth/v2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok"})
    )
    req = respx.get(f"{SDP}/api/v3/requests").mock(return_value=httpx.Response(200, json=ok_list(1)))
    respx.get(f"{SDP}/api/v3/assets").mock(return_value=httpx.Response(200, json=ok_list(1)))
    respx.get(f"{SDP}/api/v3/users").mock(return_value=httpx.Response(200, json=ok_list(1)))
    ctx = ConnectionContext(
        SDP,
        SdpConfig(edition="cloud"),
        Purpose.READ,
        "oauth2_refresh_token",
        {"client_id": "cid"},
        {"client_secret": "cs", "refresh_token": "rt"},
        5,
    )
    report = await SdpConnector().test_connection(ctx)
    assert report.ok
    assert token.called
    assert req.calls[0].request.headers["authorization"] == "Zoho-oauthtoken tok"


def bs_ctx(purpose: Purpose = Purpose.READ) -> ConnectionContext:
    return ConnectionContext(
        BS,
        BioStarConfig(verify_tls=False),
        purpose,
        "username_password",
        {"username": "admin"},
        {"password": "pw"},
        5,
    )


def _bs_reads(router: respx.MockRouter) -> None:
    router.get(f"{BS}/api/users").mock(
        return_value=httpx.Response(200, json={"UserCollection": {"total": "120"}})
    )
    router.get(f"{BS}/api/user_groups").mock(
        return_value=httpx.Response(200, json={"UserGroupCollection": {"total": "5"}})
    )
    router.get(f"{BS}/api/access_groups").mock(
        return_value=httpx.Response(200, json={"AccessGroupCollection": {"total": "3"}})
    )
    router.get(f"{BS}/api/devices").mock(
        return_value=httpx.Response(200, json={"DeviceCollection": {"total": "8"}})
    )


@respx.mock
async def test_biostar_login_session_and_logout() -> None:
    login = respx.post(f"{BS}/api/login").mock(
        return_value=httpx.Response(
            200,
            headers={"bs-session-id": "sess-1"},
            json={
                "User": {
                    "name": "Administrator",
                    "login_id": "admin",
                    "permission": {"name": "Administrator"},
                }
            },
        )
    )
    _bs_reads(respx.mock)
    logout = respx.post(f"{BS}/api/logout").mock(return_value=httpx.Response(200, json={}))
    report = await BioStarConnector().test_connection(bs_ctx())
    assert report.ok
    assert login.calls[0].request.content == b'{"User":{"login_id":"admin","password":"pw"}}'
    users_call = respx.mock.routes[1].calls[0].request
    assert users_call.headers["bs-session-id"] == "sess-1"
    assert report.info["role"] == "Administrator"
    assert [p.detail for p in report.permissions] == ["120 bản ghi", "5 bản ghi", "3 bản ghi", "8 bản ghi"]
    assert logout.called


@respx.mock
async def test_biostar_bad_password() -> None:
    respx.post(f"{BS}/api/login").mock(
        return_value=httpx.Response(401, json={"Response": {"code": "1", "message": "Login failed"}})
    )
    report = await BioStarConnector().test_connection(bs_ctx())
    assert not report.ok
    assert "Login failed" in report.steps[-1].detail


@respx.mock
async def test_biostar_relogin_on_expired_session() -> None:
    from app.core.connectors.biostar import BioStarClient

    login = respx.post(f"{BS}/api/login").mock(
        side_effect=[
            httpx.Response(200, headers={"bs-session-id": "s1"}, json={"User": {}}),
            httpx.Response(200, headers={"bs-session-id": "s2"}, json={"User": {}}),
        ]
    )
    respx.get(f"{BS}/api/users").mock(
        side_effect=[
            httpx.Response(401, json={"Response": {"message": "expired"}}),
            httpx.Response(200, json={"UserCollection": {"total": "1"}}),
        ]
    )
    respx.post(f"{BS}/api/logout").mock(return_value=httpx.Response(200, json={}))
    async with BioStarClient(bs_ctx()) as client:
        data = await client.get("/api/users")
        assert client.session_id == "s2"
    assert data["UserCollection"]["total"] == "1"
    assert login.call_count == 2
