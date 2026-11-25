"""Tồn kho: new / returned / waiting for disposal, bulk SDP state changes, disposal reserved to managers."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import respx

from app.core.db import get_sessionmaker
from app.core.workflow.engine import pump
from tests.lifecycle_fixtures import ASSETS, sdp_ok
from tests.personal_keys import give_personal_keys
from tests.test_lifecycle_api import SDP, _sync, configured, mock_systems  # noqa: F401 (fixture)


async def _user(admin, role: str, upn: str) -> None:  # type: ignore[no-untyped-def]
    roles = {x["code"]: x["id"] for x in (await admin.get("/api/core/roles")).json()}
    if upn not in {u["upn"] for u in (await admin.get("/api/core/users")).json()}:
        r = await admin.post(
            "/api/core/users", json={"upn": upn, "display_name": upn, "role_ids": [roles[role]]}
        )
        assert r.status_code == 201, r.text


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_stock_statuses_and_state_changes(admin, client, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    sdp_profile, _ = configured
    routes = mock_systems(respx_mock)
    await _sync()
    resp = await admin.get("/api/lifecycle/stock")
    assert resp.status_code == 200, resp.text
    stock = resp.json()
    by_id = {i["id"]: i for i in stock["items"]}
    # In Store, unassigned → stock; a2 has no serial on SDP → flagged.
    assert by_id["a2"]["status"] == "new" and by_id["a2"]["missing"] == ["serial"]
    assert "a1" not in by_id  # in use
    assert stock["can"] == {"manage": True, "dispose": True}

    # The sync marks an asset that came back In Store from someone as returned (thu hồi).
    back = [{**a, "state": {"name": "In Store"}, "user": None} if a["id"] == "a1" else a for a in ASSETS]
    routes["assets"].mock(return_value=httpx.Response(200, json=sdp_ok("assets", back)))
    await _sync()
    a1 = {i["id"]: i for i in (await admin.get("/api/lifecycle/stock")).json()["items"]}["a1"]
    assert a1["status"] == "returned" and a1["returned_from"] == "Phạm Thị Bình"

    # Manual mark / unmark (infra-ops only).
    r = await admin.post("/api/lifecycle/stock/returned", json={"asset_ids": ["a2"], "returned": True})
    assert r.status_code == 200
    assert {i["id"]: i for i in (await admin.get("/api/lifecycle/stock")).json()["items"]}["a2"][
        "status"
    ] == "returned"

    # Roles: operators view only; supervisors move to "Chờ thanh lý" but never dispose.
    await _user(admin, "operator", "stock-op@test.local")
    await _user(admin, "supervisor", "stock-sup@test.local")
    await client.login("stock-op@test.local")
    body = {"assets": [{"id": "a2", "tag": "MN0001"}], "to": "pending"}
    assert (await client.post("/api/lifecycle/stock/state", json=body)).status_code == 403
    await client.login("stock-sup@test.local")
    denied = await client.post("/api/lifecycle/stock/state", json={**body, "to": "disposed"})
    assert denied.status_code == 403 and "Manager hoặc Administrator" in denied.json()["error"]["message"]

    # Pending disposal: a worker job reads then writes SDP with the requester's own key.
    await give_personal_keys(sdp_profile["id"], "admin@test.local")
    state = {"a2": "In Store"}
    puts: list[dict] = []  # type: ignore[type-arg]

    def asset(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            body = json.loads(parse_qs(request.content.decode())["input_data"][0])["asset"]
            puts.append({"auth": request.headers["authtoken"], **body})
            state["a2"] = body["state"]["name"]
        ok = {"response_status": {"status_code": 2000, "status": "success"}}
        return httpx.Response(200, json={**ok, "asset": {"id": "a2", "state": {"name": state["a2"]}}})

    respx_mock.route(url__regex=rf"{SDP}/api/v3/assets/a2$").mock(side_effect=asset)
    # Disposing something not waiting for disposal is refused.
    early = await admin.post("/api/lifecycle/stock/state", json={**body, "to": "disposed"})
    assert early.json()["error"]["code"] == "not_pending_disposal"
    job = (await admin.post("/api/lifecycle/stock/state", json=body)).json()["job_id"]
    async with get_sessionmaker()() as s:
        await pump(s)
    j = (await admin.get(f"/api/core/jobs/{job}")).json()
    assert j["status"] == "completed", [(x["status"], x["message"]) for x in j["steps"]]
    assert puts == [
        {"auth": "key-admin@test.local", "state": {"name": "Chờ thanh lý"}}
    ]  # state only, user untouched

    # After the next sync it shows as waiting; a manager/admin disposes it.
    pending = [{**a, "state": {"name": "Chờ thanh lý"}} if a["id"] == "a2" else a for a in back]
    routes["assets"].mock(return_value=httpx.Response(200, json=sdp_ok("assets", pending)))
    await _sync()
    assert {i["id"]: i for i in (await admin.get("/api/lifecycle/stock")).json()["items"]}["a2"][
        "status"
    ] == "pending"
    job = (await admin.post("/api/lifecycle/stock/state", json={**body, "to": "disposed"})).json()["job_id"]
    async with get_sessionmaker()() as s:
        await pump(s)
    assert (await admin.get(f"/api/core/jobs/{job}")).json()["status"] == "completed"
    assert puts[-1]["state"] == {"name": "Disposed"}
