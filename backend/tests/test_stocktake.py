"""Year-end IT asset stocktake: SDP book → site count → differences handled → closed; Excel for HC / KT."""

from __future__ import annotations

import io
import uuid

import respx
from openpyxl import load_workbook

from app.modules.lifecycle.stocktake import (
    area_for,
    book_item,
    difference,
    in_book,
    matches,
    summary,
    zone_progress,
)
from tests.test_lifecycle_api import _sync, configured, mock_systems  # noqa: F401  (fixture)
from tests.test_workload import _user


def test_book_item_takes_the_accounting_code_from_the_chosen_custom_field() -> None:
    asset = {"id": "a1", "name": "LT", "serial": "ORG-1", "asset_tag": "LT1", "udf": {"udf_char1": "TSCD-9"}}
    assert book_item(asset, "udf_char1")["fa_code"] == "TSCD-9"
    assert book_item(asset, "")["fa_code"] is None
    assert not in_book({"state": "disposed"}, "Disposed") and in_book({"state": "In Use"}, "Disposed")


def test_codes_match_serial_tag_or_accounting_code_case_insensitively() -> None:
    item = {"serial": "ab-12", "asset_tag": "LT0123", "fa_code": None}
    assert matches(item, " AB-12 ") and matches(item, "lt0123")
    assert not matches(item, "") and not matches(item, "LT01")


def test_differences_and_summary() -> None:
    items = [
        {"count": "found", "condition": "good"},
        {"count": "found", "condition": "broken", "resolution": "dispose"},
        {"count": "missing"},
        {"count": "extra", "condition": "good"},
        {"count": "pending"},
    ]
    assert [difference(i) is not None for i in items] == [False, True, True, True, False]
    assert summary(items) == {
        "book": 4, "pending": 1, "found": 2, "missing": 1, "extra": 1, "differences": 3, "unresolved": 2
    }  # fmt: skip


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_stocktake_end_to_end(admin, client, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    mock_systems(respx_mock)
    assert (await _sync())["status"] in ("success", "partial")

    created = await admin.post(
        "/api/lifecycle/stocktakes", json={"name": "Kiểm kê 2026", "cutoff": "2026-12-31"}
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    base = f"/api/lifecycle/stocktakes/{sid}"
    st = (await admin.get(base)).json()
    assert st["status"] == "counting" and st["summary"]["book"] == len(st["items"]) > 0
    laptop = next(i for i in st["items"] if i["asset_id"] == "a1")
    assert (laptop["serial"], laptop["asset_tag"], laptop["count"]) == ("SN1", "LT0123", "pending")

    # Scan by serial (any case), again (idempotent), and an unknown code.
    hit = (await admin.post(f"{base}/scan", json={"code": "sn1", "seen_at": "Tầng 3"})).json()
    assert hit["matched"] and not hit["already"] and hit["item"]["count"] == "found"
    assert (await admin.post(f"{base}/scan", json={"code": "LT0123"})).json()["already"] is True
    assert (await admin.post(f"{base}/scan", json={"code": "NOPE"})).json()["matched"] is False

    # Found but broken; an asset on site that SDP does not know (a duplicate code is refused).
    r = await admin.put(f"{base}/items/{laptop['id']}", json={"count": "found", "condition": "broken"})
    assert r.json()["difference"] == "Tình trạng: Hỏng, không dùng được"
    extra = await admin.post(
        f"{base}/extras", json={"name": "Màn hình lạ", "serial": "X-1", "seen_at": "Kho"}
    )
    assert extra.status_code == 201 and extra.json()["count"] == "extra"
    dup = await admin.post(f"{base}/extras", json={"name": "Trùng", "asset_tag": "lt0123"})
    assert dup.json()["error"]["code"] == "extra_duplicate"

    # Resolutions come after the count; closing needs every difference handled.
    assert (await admin.post(f"{base}/close")).json()["error"]["code"] == "not_reviewing"
    assert (await admin.post(f"{base}/finish-count")).json()["status"] == "reviewing"
    st = (await admin.get(base)).json()
    assert st["summary"]["pending"] == 0 and st["summary"]["missing"] == st["summary"]["book"] - 1
    assert (await admin.post(f"{base}/close")).json()["error"]["code"] == "differences_unresolved"
    needs = [i for i in st["items"] if i["difference"]]
    bad = await admin.put(f"{base}/items/{needs[0]['id']}/resolution", json={"resolution": "investigate"})
    assert bad.json()["error"]["code"] == "resolution_note_required"
    for i in needs:
        r = await admin.put(
            f"{base}/items/{i['id']}/resolution", json={"resolution": "update_sdp", "note": "ok"}
        )
        assert r.status_code == 200, r.text
    closed = await admin.post(f"{base}/close")
    assert closed.json()["status"] == "closed", closed.text
    assert (await admin.post(f"{base}/scan", json={"code": "SN1"})).json()["error"][
        "code"
    ] == "stocktake_closed"

    # Excel for Hành chánh / Kế toán.
    x = await admin.get(f"{base}/export")
    assert x.status_code == 200 and "kiem-ke-20261231.xlsx" in x.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Tổng hợp", "Sổ SDP", "Chênh lệch", "Ngoài sổ SDP"]
    assert [r[1:3] for r in wb["Ngoài sổ SDP"].iter_rows(min_row=2, values_only=True)] == [
        ("Chưa phân khu", "Màn hình lạ")
    ]
    actions = (await admin.get("/api/core/audit", params={"action": "lifecycle.stocktake_closed"})).json()
    assert actions["items"]

    # Viewers only look: no counting, no opening a stocktake.
    await _user(admin, "viewer", "st-viewer@test.local")
    await client.login("st-viewer@test.local")
    assert (await client.get(base)).status_code == 200
    assert (await client.post(f"{base}/scan", json={"code": "SN1"})).status_code == 403
    assert (
        await client.post("/api/lifecycle/stocktakes", json={"name": "x", "cutoff": "2026-12-31"})
    ).status_code == 403


def test_area_by_listed_employee_then_department_and_store_for_unused_assets() -> None:
    areas = [
        {"name": "Tầng 5", "departments": ["Kế Toán"], "employee_ids": []},
        {"name": "Tầng 6", "departments": [], "employee_ids": ["002100"]},
        {"name": "Kho IT", "is_store": True, "departments": [], "employee_ids": []},
    ]
    assert area_for({"employee_id": "002100", "department": "Kế Toán"}, areas) == "Tầng 6"  # listed ID wins
    assert area_for({"employee_id": "009999", "department": "kế toán "}, areas) == "Tầng 5"
    assert area_for({"employee_id": "009999", "department": "Kinh Doanh"}, areas) is None
    assert area_for(None, areas) == "Kho IT"
    assert area_for(None, areas[:2]) is None
    zones = [{"id": "z1", "name": "Tầng 5", "assignee_name": "An"}]
    rows = zone_progress(zones, [{"zone_id": "z1", "count": "found"}, {"zone_id": None, "count": "pending"}])
    assert [(r["name"], r["found"], r["pending"]) for r in rows] == [
        ("Tầng 5", 1, 0),
        ("Chưa phân khu", 0, 1),
    ]


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_areas_split_the_count_between_counters(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    mock_systems(respx_mock)
    await _sync()
    areas = "/api/lifecycle/stocktake-areas"
    t5 = await admin.post(areas, json={"name": "Tầng 5", "departments": ["Kế Toán"], "position": 1})
    assert t5.status_code == 201, t5.text
    store = await admin.post(areas, json={"name": "Kho IT", "is_store": True, "position": 9})
    assert store.status_code == 201, store.text
    # One department, employee ID and store role per area; IDs keep their leading zeros.
    taken = await admin.post(areas, json={"name": "Tầng 6", "departments": ["kế toán"]})
    assert taken.json()["error"]["code"] == "department_taken"
    assert (await admin.post(areas, json={"name": "Kho 2", "is_store": True})).json()["error"][
        "code"
    ] == "store_area_taken"
    assert (await admin.post(areas, json={"name": "X", "employee_ids": ["00 1"]})).json()["error"][
        "code"
    ] == "bad_employee_ids"
    t6 = (await admin.post(areas, json={"name": "Tầng 6", "employee_ids": ["1500"]})).json()
    assert t6["employee_ids"] == ["001500"]

    listing = (await admin.get(areas)).json()
    assert {a["name"]: a["assets"] for a in listing["areas"]}["Tầng 5"] >= 1  # a1 (Kế Toán)
    assert any(d["name"] == "Kế Toán" for d in listing["departments"])

    st = (
        await admin.post(
            "/api/lifecycle/stocktakes", json={"name": "Kiểm kê theo tầng", "cutoff": "2026-12-31"}
        )
    ).json()
    base = f"/api/lifecycle/stocktakes/{st['id']}"
    got = (await admin.get(base)).json()
    zones = {z["name"]: z for z in got["zones"]}
    by_asset = {i["asset_id"]: i for i in got["items"]}
    assert by_asset["a1"]["zone_id"] == zones["Tầng 5"]["id"] and by_asset["a1"]["employee_id"] == "002100"
    assert by_asset["a2"]["zone_id"] == zones["Kho IT"]["id"]  # nobody uses it

    # Hand a zone to a counter; someone without the count permission is refused.
    counters = (await admin.get("/api/lifecycle/stocktake-counters")).json()
    me = next(c for c in counters if c["upn"] == "admin@test.local")
    r = await admin.put(f"{base}/zones/{zones['Tầng 5']['id']}", json={"user_id": me["id"]})
    assert r.json()["assignee_name"] == me["name"]
    assert (
        await admin.put(f"{base}/zones/{zones['Tầng 5']['id']}", json={"user_id": str(uuid.uuid4())})
    ).json()["error"]["code"] == "not_a_counter"
    # Move an asset to another zone.
    moved = await admin.put(
        f"{base}/items/{by_asset['a1']['id']}/zone", json={"zone_id": zones["Tầng 6"]["id"]}
    )
    assert moved.json()["zone_id"] == zones["Tầng 6"]["id"]
    got = (await admin.get(base)).json()
    assert {z["name"]: z["book"] for z in got["zones"]}["Tầng 6"] >= 1
    x = await admin.get(f"{base}/export")
    assert "Theo khu" in load_workbook(io.BytesIO(x.content)).sheetnames

    for a in (await admin.get(areas)).json()["areas"]:  # leave no areas behind for other tests
        assert (await admin.delete(f"{areas}/{a['id']}")).status_code == 204
