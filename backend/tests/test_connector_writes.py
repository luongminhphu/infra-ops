"""Write helpers of the SDP / BioStar clients, against mocked servers."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.core.connectors.base import ConnectionContext, Purpose
from app.core.connectors.biostar import BioStarClient, BioStarConfig, BioStarError
from app.core.connectors.sdp import SdpClient, SdpConfig

SDP = "https://sdp-w.test.local"
BS = "https://bs-w.test.local"
OK = {"response_status": {"status_code": 2000, "status": "success"}}


def sdp() -> SdpClient:
    return SdpClient(ConnectionContext(SDP, SdpConfig(), Purpose.WRITE, "api_key", {}, {"api_key": "w"}, 5))


def bs() -> BioStarClient:
    return BioStarClient(
        ConnectionContext(
            BS, BioStarConfig(), Purpose.WRITE, "username_password", {"username": "svc"}, {"password": "p"}, 5
        )
    )


def form(request: httpx.Request) -> dict:  # type: ignore[type-arg]
    return json.loads(parse_qs(request.content.decode())["input_data"][0])


@respx.mock
async def test_sdp_update_asset_sends_input_data_form() -> None:
    route = respx.put(f"{SDP}/api/v3/assets/55").mock(
        return_value=httpx.Response(200, json={**OK, "asset": {"id": "55"}})
    )
    async with sdp() as c:
        await c.update_asset("55", "In Use", "u1")
        await c.update_asset("55", "In Store", None)
    first, second = (form(call.request) for call in route.calls)
    assert first == {"asset": {"state": {"name": "In Use"}, "user": {"id": "u1"}}}
    assert second == {"asset": {"state": {"name": "In Store"}, "user": None}}
    assert route.calls[0].request.headers["authtoken"] == "w"


@respx.mock
async def test_sdp_note_and_status() -> None:
    note = respx.post(f"{SDP}/api/v3/requests/9/notes").mock(return_value=httpx.Response(200, json=OK))
    status = respx.put(f"{SDP}/api/v3/requests/9").mock(return_value=httpx.Response(200, json=OK))
    async with sdp() as c:
        await c.add_note("9", "<p>Đã bàn giao</p>")
        await c.set_request_status("9", "Resolved")
    assert form(note.calls[0].request)["note"]["description"] == "<p>Đã bàn giao</p>"
    assert form(status.calls[0].request) == {"request": {"status": {"name": "Resolved"}}}


@respx.mock
async def test_sdp_write_not_retried_on_503() -> None:
    route = respx.put(f"{SDP}/api/v3/assets/1").mock(return_value=httpx.Response(503, json={}))
    async with sdp() as c:
        with pytest.raises(Exception):  # noqa: B017
            await c.update_asset("1", "In Use", "u")
    assert route.call_count == 1


@respx.mock
async def test_biostar_user_crud_and_errors() -> None:
    respx.post(f"{BS}/api/login").mock(
        return_value=httpx.Response(200, headers={"bs-session-id": "s"}, json={"User": {}})
    )
    respx.get(f"{BS}/api/users/003551").mock(
        return_value=httpx.Response(404, json={"Response": {"code": "1"}})
    )
    create = respx.post(f"{BS}/api/users").mock(
        return_value=httpx.Response(200, json={"Response": {"code": "0"}})
    )
    update = respx.put(f"{BS}/api/users/003551").mock(
        return_value=httpx.Response(200, json={"Response": {"code": "0"}})
    )
    delete = respx.delete(f"{BS}/api/users").mock(
        return_value=httpx.Response(200, json={"Response": {"code": "0"}})
    )
    respx.post(f"{BS}/api/logout").mock(return_value=httpx.Response(200, json={}))
    async with bs() as c:
        assert await c.get_user("003551") is None
        await c.create_user({"user_id": "003551", "name": "A"})
        await c.update_user("003551", {"disabled": "true"})
        await c.delete_user("003551")
    assert json.loads(create.calls[0].request.content) == {"User": {"user_id": "003551", "name": "A"}}
    assert json.loads(update.calls[0].request.content) == {"User": {"disabled": "true"}}
    assert delete.calls[0].request.url.params["id"] == "003551"


@respx.mock
async def test_biostar_error_code_in_body_is_an_error() -> None:
    respx.post(f"{BS}/api/login").mock(
        return_value=httpx.Response(200, headers={"bs-session-id": "s"}, json={"User": {}})
    )
    respx.post(f"{BS}/api/users").mock(
        return_value=httpx.Response(200, json={"Response": {"code": "6", "message": "Duplicate user ID"}})
    )
    respx.post(f"{BS}/api/logout").mock(return_value=httpx.Response(200, json={}))
    async with bs() as c:
        with pytest.raises(BioStarError, match="Duplicate user ID"):
            await c.create_user({"user_id": "1"})


@respx.mock
async def test_sdp_list_all_drops_records_repeated_across_pages() -> None:
    # Offset paging over a shifting list can hand back a record twice (seen on a real SDP: every user doubled).
    pages = [
        {"users": [{"id": "1"}, {"id": "2"}], "list_info": {"has_more_rows": True}},
        {"users": [{"id": "2"}, {"id": "3"}], "list_info": {"has_more_rows": False}},
    ]
    respx.get(f"{SDP}/api/v3/users").mock(side_effect=[httpx.Response(200, json=p) for p in pages])
    async with sdp() as c:
        rows = await c.list_all("/api/v3/users", "users", page_size=2)
    assert [r["id"] for r in rows] == ["1", "2", "3"]


@respx.mock
async def test_sdp_list_all_sorts_by_id_and_falls_back_when_refused() -> None:
    """Paging needs a stable order (else SDP skips records); an SDP refusing the sort is read unsorted."""
    seen: list[dict] = []  # type: ignore[type-arg]

    def users(request: httpx.Request) -> httpx.Response:
        info = json.loads(request.url.params["input_data"])["list_info"]
        seen.append(info)
        if info.get("sort_field"):
            return httpx.Response(
                400, json={"response_status": {"status": "failed", "messages": [{"message": "Invalid sort"}]}}
            )
        return httpx.Response(200, json={"users": [{"id": "1"}], "list_info": {"has_more_rows": False}})

    respx.get(f"{SDP}/api/v3/users").mock(side_effect=users)
    async with sdp() as c:
        rows = await c.list_all("/api/v3/users", "users", sort_field="id", sort_order="asc")
    assert [r["id"] for r in rows] == ["1"]
    assert seen[0]["sort_field"] == "id" and seen[0]["sort_order"] == "asc" and seen[0]["get_total_count"]
    assert "sort_field" not in seen[1]
