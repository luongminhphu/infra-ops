"""Year-end IT asset stocktake: SDP book → site count → differences handled → closed; Excel for HC / KT."""

from __future__ import annotations

import io
import uuid
from datetime import date

import respx
from openpyxl import load_workbook
from sqlalchemy import select

from app.modules.lifecycle.stocktake import (
    area_for,
    book_item,
    difference,
    in_book,
    matches,
    summary,
    to_xlsx,
    zone_progress,
)
from tests.test_documents import docx_text
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
    padded = (await admin.post(f"{base}/scan", json={"code": "\t lt0123 "})).json()
    assert padded["matched"] and padded["already"] and padded["item"]["id"] == laptop["id"]

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
    missing_one = next(i for i in needs if i["count"] == "missing")
    bad = await admin.put(f"{base}/items/{missing_one['id']}/resolution", json={"resolution": "investigate"})
    assert bad.json()["error"]["code"] == "resolution_note_required"
    for i in needs:
        res = (
            "update_sdp" if i["count"] != "found" else "repair"
        )  # broken assets are repaired, not "fixed in SDP"
        r = await admin.put(f"{base}/items/{i['id']}/resolution", json={"resolution": res, "note": "ok"})
        assert r.status_code == 200, r.text
    closed = await admin.post(f"{base}/close")
    assert closed.json()["status"] == "closed", closed.text
    assert (await admin.post(f"{base}/scan", json={"code": "SN1"})).json()["error"][
        "code"
    ] == "stocktake_closed"

    # Biên bản kiểm kê signed by IT and Kế toán: drafted from the closed stocktake, refreshed while a draft.
    who = {"it_rep": "Lê Minh Tuấn", "it_title": "Trưởng nhóm IT", "finance_rep": "Phạm Thu Hà"}
    rec = await admin.post(f"{base}/record", json=who)
    assert rec.status_code == 200, rec.text
    doc_id = rec.json()["id"]
    doc = (await admin.get(f"/api/core/documents/{doc_id}")).json()
    assert doc["kind"] == "lifecycle.stocktake" and doc["status"] == "draft" and doc["external_ref"] == sid
    assert doc["data"]["totals"]["extra"] == 1 and len(doc["data"]["differences"]) == len(needs)
    assert doc["data"]["differences"][0]["resolution"] in ("Sửa SDP (làm tay) – ok", "Sửa chữa – ok")
    assert doc["data"]["totals"]["damaged"] == 1 and doc["data"]["totals"]["damaged_light"] == 0
    assert len(doc["data"]["assets"]) == len(st["items"])  # phụ lục: every asset
    again = await admin.post(f"{base}/record", json={**who, "finance_title": "Kế toán TSCĐ"})
    assert (
        again.json()["id"] == doc_id
        and (await admin.get(base)).json()["record"]["data"]["finance_title"] == "Kế toán TSCĐ"
    )

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
    # K-12: the record lists every zone, so an Operator neither gets it in the stocktake nor opens the document.
    await _user(admin, "operator", "st-op-rec@test.local")
    await client.login("st-op-rec@test.local")
    assert (await client.get(base)).json()["record"] is None
    assert (await client.get(f"/api/core/documents/{doc_id}")).status_code == 403


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
async def test_areas_split_the_count_between_counters(admin, client, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
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
    # The counter sees the zone in "Việc tiếp theo" while assets are left to check.
    nxt = (await admin.get("/api/core/me/next")).json()["items"]
    mine = [i for i in nxt if i["kind"] == "stocktake"]
    assert (
        mine
        and mine[0]["title"].startswith("Kiểm kê Tầng 5")
        and mine[0]["href"] == f"/lifecycle/stocktake/{st['id']}"
    )
    # Operators walk their floors only: no Excel files, no area catalogue (the manager's picture).
    await _user(admin, "operator", "st-op@test.local")
    await client.login("st-op@test.local")
    assert (await client.get(base)).status_code == 200
    assert (await client.get(f"{base}/export")).status_code == 403
    assert (await client.get("/api/lifecycle/stocktake-areas")).status_code == 403
    # …but an Operator downloads the file of a zone handed to them, and only that one.
    op_id = next(
        c["id"]
        for c in (await admin.get("/api/lifecycle/stocktake-counters")).json()
        if c["upn"] == "st-op@test.local"
    )
    await admin.put(f"{base}/zones/{zones['Tầng 6']['id']}", json={"user_id": op_id})
    own = await client.get(f"{base}/export", params={"zone_id": zones["Tầng 6"]["id"]})
    assert own.status_code == 200 and "tang-6" in own.headers["content-disposition"]
    assert (await client.get(f"{base}/export", params={"zone_id": zones["Kho IT"]["id"]})).status_code == 403
    # Supervisors hand out zones too (K-09); Operators do not.
    await _user(admin, "supervisor", "st-sup@test.local")
    await client.login("st-sup@test.local")
    assert (await client.get(base)).status_code == 200
    assert (
        await client.put(f"{base}/zones/{zones['Tầng 5']['id']}", json={"user_id": None})
    ).status_code == 200
    assert (await client.get("/api/lifecycle/stocktake-counters")).status_code == 200
    await client.login("st-op@test.local")
    assert (
        await client.put(f"{base}/zones/{zones['Tầng 5']['id']}", json={"user_id": None})
    ).status_code == 403
    # Move an asset to another zone.
    moved = await admin.put(
        f"{base}/items/{by_asset['a1']['id']}/zone", json={"zone_id": zones["Tầng 6"]["id"]}
    )
    assert moved.json()["zone_id"] == zones["Tầng 6"]["id"]
    got = (await admin.get(base)).json()
    assert {z["name"]: z["book"] for z in got["zones"]}["Tầng 6"] >= 1
    x = await admin.get(f"{base}/export")
    assert "Theo khu" in load_workbook(io.BytesIO(x.content)).sheetnames
    # One zone's part (file thành phần) next to the whole (file tổng).
    part = await admin.get(f"{base}/export", params={"zone_id": zones["Kho IT"]["id"]})
    assert "kiem-ke-20261231-kho-it.xlsx" in part.headers["content-disposition"]
    rows = list(load_workbook(io.BytesIO(part.content))["Sổ SDP"].iter_rows(min_row=2, values_only=True))
    assert rows and {r[1] for r in rows} == {"Kho IT"}
    assert (await admin.post(f"{base}/record", json={"it_rep": "a", "finance_rep": "b"})).json()["error"][
        "code"
    ] == "not_closed"

    for a in (await admin.get(areas)).json()["areas"]:  # leave no areas behind for other tests
        assert (await admin.delete(f"{areas}/{a['id']}")).status_code == 204


def test_stocktake_record_template_uses_documented_placeholders() -> None:
    from app.core.documents.engine import template_variables
    from app.modules.lifecycle.stocktake_record import STOCKTAKE_RECORD

    assert template_variables(STOCKTAKE_RECORD.default_template.read_bytes()) == {
        "assets", "logo", "number", "title", "cutoff", "date", "synced_at", "totals", "zones", "differences",
        "it_rep", "it_title", "finance_rep", "finance_title", "conclusion",
    }  # fmt: skip


def test_light_damage_is_a_difference_only_under_rules_version_1() -> None:
    light = {"count": "found", "condition": "damaged"}
    assert difference(light, 1) == "Tình trạng: Hư hỏng nhẹ"
    assert difference(light, 2) is None and difference(light) is None
    assert difference({"count": "found", "condition": "broken"}, 2) is not None
    assert summary([light], 1)["differences"] == 1 and summary([light], 2)["differences"] == 0
    # a suggested resolution is still unresolved
    sug = {"count": "missing", "resolution": "investigate", "resolution_suggested": True}
    assert summary([sug])["unresolved"] == 1
    assert summary([{**sug, "resolution_suggested": False}])["unresolved"] == 0


async def _open_stocktake(admin, name: str):  # type: ignore[no-untyped-def]
    st = (await admin.post("/api/lifecycle/stocktakes", json={"name": name, "cutoff": "2026-12-31"})).json()
    return f"/api/lifecycle/stocktakes/{st['id']}", st["id"]


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_default_resolutions_wait_for_confirmation_and_types_limit_resolutions(  # type: ignore[no-untyped-def]
    admin,
    client,
    configured,  # noqa: F811
    respx_mock,
) -> None:
    mock_systems(respx_mock)
    await _sync()
    base, _ = await _open_stocktake(admin, "Mặc định")
    items = (await admin.get(base)).json()["items"]
    laptop = next(i for i in items if i["asset_id"] == "a1")
    other = next(i for i in items if i["asset_id"] != "a1")
    # rules_version 2: light damage is not a difference; broken is.
    r = await admin.put(f"{base}/items/{other['id']}", json={"count": "found", "condition": "damaged"})
    assert r.json()["difference"] is None
    await admin.put(f"{base}/items/{laptop['id']}", json={"count": "found", "condition": "broken"})
    await admin.post(f"{base}/extras", json={"name": "Lạ", "serial": "X-9"})
    assert (await admin.post(f"{base}/default-resolutions")).json()["error"]["code"] == "not_reviewing"
    await admin.post(f"{base}/finish-count")
    st = (await admin.get(base)).json()
    assert st["rules_version"] == 2 and other["id"] not in [i["id"] for i in st["items"] if i["difference"]]
    r = await admin.post(f"{base}/default-resolutions")
    assert r.status_code == 200 and r.json()["broken"] == 1 and r.json()["extra"] == 1, r.text
    st = (await admin.get(base)).json()
    sug = {i["id"]: i for i in st["items"] if i["difference"]}
    assert all(i["resolution_suggested"] and i["resolved_by"] is None for i in sug.values())
    assert sug[laptop["id"]]["resolution"] == "dispose"
    assert any(i["resolution"] == "investigate" and i["resolution_note"] for i in sug.values())
    assert st["summary"]["unresolved"] == st["summary"]["differences"]  # suggestions are not decisions
    assert (await admin.post(f"{base}/close")).json()["error"]["code"] == "differences_unresolved"
    audit = (
        await admin.get("/api/core/audit", params={"action": "lifecycle.stocktake_default_resolutions"})
    ).json()
    assert len(audit["items"]) == 1
    # Resolutions follow the kind of difference.
    bad = await admin.put(f"{base}/items/{laptop['id']}/resolution", json={"resolution": "update_sdp"})
    assert bad.json()["error"]["code"] == "resolution_not_allowed"
    extra_id = next(i["id"] for i in sug.values() if i["count"] == "extra")
    bad = await admin.put(f"{base}/items/{extra_id}/resolution", json={"resolution": "repair"})
    assert bad.json()["error"]["code"] == "resolution_not_allowed"
    for i in sug.values():  # confirming (even the same value) clears the flag
        r = await admin.put(
            f"{base}/items/{i['id']}/resolution",
            json={"resolution": i["resolution"], "note": i["resolution_note"]},
        )
        assert r.status_code == 200 and r.json()["resolution_suggested"] is False
    assert (await admin.post(f"{base}/close")).json()["status"] == "closed"


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_scan_moves_pending_items_and_batch_is_all_or_nothing_and_scoped(  # type: ignore[no-untyped-def]
    admin,
    client,
    configured,  # noqa: F811
    respx_mock,
) -> None:
    mock_systems(respx_mock)
    await _sync()
    areas = "/api/lifecycle/stocktake-areas"
    await admin.post(areas, json={"name": "Tầng 5", "departments": ["Kế Toán"], "position": 1})
    await admin.post(areas, json={"name": "Kho IT", "is_store": True, "position": 9})
    await admin.post(areas, json={"name": "Tầng 6", "employee_ids": ["1500"], "position": 2})
    base, _ = await _open_stocktake(admin, "Quét")
    st = (await admin.get(base)).json()
    zones = {z["name"]: z for z in st["zones"]}
    by_asset = {i["asset_id"]: i for i in st["items"]}
    a1, a2 = by_asset["a1"], by_asset["a2"]
    assert a1["zone_id"] == zones["Tầng 5"]["id"] and a2["zone_id"] == zones["Kho IT"]["id"]

    # Scanned in another zone while pending: moves, keeps where it was booked, audited with from/to.
    other_stocktake, _ = await _open_stocktake(admin, "Khác")
    foreign = (await admin.get(other_stocktake)).json()["zones"][0]["id"]
    bad = await admin.post(f"{base}/scan", json={"code": "SN1", "zone_id": foreign})
    assert bad.status_code == 404
    hit = (await admin.post(f"{base}/scan", json={"code": "SN1", "zone_id": zones["Tầng 6"]["id"]})).json()
    assert hit["item"]["zone_id"] == zones["Tầng 6"]["id"] and hit["item"]["seen_at"] == "Tầng 5"
    rows = (await admin.get("/api/core/audit", params={"action": "lifecycle.stocktake_counted"})).json()[
        "items"
    ]
    moved = next(r for r in rows if r["after"].get("zone") == "Tầng 6")
    assert moved["before"]["zone"] == "Tầng 5"
    # Already found: a second scan elsewhere does not move it.
    again = (await admin.post(f"{base}/scan", json={"code": "SN1", "zone_id": zones["Kho IT"]["id"]})).json()
    assert again["already"] and again["item"]["zone_id"] == zones["Tầng 6"]["id"]

    # Batch: whole desk at once; all-or-nothing; every id must belong to the stocktake.
    ok = await admin.post(f"{base}/count-batch", json={"item_ids": [a2["id"]], "count": "found"})
    assert ok.status_code == 200 and ok.json()["items"][0]["count"] == "found"
    fresh = (await admin.get(base)).json()["items"]
    pend = [i["id"] for i in fresh if i["count"] == "pending"]
    assert pend
    bad = await admin.post(f"{base}/count-batch", json={"item_ids": [pend[0], 999999999], "count": "found"})
    assert bad.status_code == 404
    assert (
        next(i for i in (await admin.get(base)).json()["items"] if i["id"] == pend[0])["count"] == "pending"
    )
    # K-17: a batch never overwrites an asset already counted; it reports how many it skipped.
    again = await admin.post(
        f"{base}/count-batch", json={"item_ids": [a2["id"], pend[0]], "count": "missing"}
    )
    assert again.json()["skipped"] == 1
    states = {i["id"]: i["count"] for i in again.json()["items"]}
    assert states == {a2["id"]: "found", pend[0]: "missing"}
    assert (
        await admin.post(f"{base}/count-batch", json={"item_ids": list(range(1, 102)), "count": "found"})
    ).status_code == 422
    # Operators: only their own zones, in the listing and in the batch.
    await _user(admin, "operator", "st-op2@test.local")
    op_id = next(
        c["id"]
        for c in (await admin.get("/api/lifecycle/stocktake-counters")).json()
        if c["upn"] == "st-op2@test.local"
    )
    await admin.put(f"{base}/zones/{zones['Kho IT']['id']}", json={"user_id": op_id})
    await client.login("st-op2@test.local")
    mine = (await client.get(base)).json()
    assert [z["name"] for z in mine["zones"]] == ["Kho IT"]
    assert mine["items"] and {i["zone_id"] for i in mine["items"]} == {zones["Kho IT"]["id"]}
    assert mine["summary"]["book"] == len(mine["items"])
    denied = await client.post(f"{base}/count-batch", json={"item_ids": [a1["id"]], "count": "found"})
    assert denied.status_code == 403
    foreign_scan = await client.post(
        f"{base}/scan", json={"code": "LT0123", "zone_id": zones["Tầng 5"]["id"]}
    )
    assert foreign_scan.status_code == 403
    await admin.post(f"{base}/finish-count")
    assert (await client.post(f"{base}/count-batch", json={"item_ids": [a2["id"]], "count": "found"})).json()[
        "error"
    ]["code"] == "not_counting"
    for a in (await admin.get(areas)).json()["areas"]:
        await admin.delete(f"{areas}/{a['id']}")


def test_supervisors_may_assign_zones_by_default() -> None:
    from app.modules.lifecycle.manifest import manifest

    perm = next(p for p in manifest.permissions if p.code == "lifecycle.stocktake.assign")
    assert perm.default_roles == ("manager", "supervisor")


def test_template_has_a_light_damage_row_and_excel_marks_suggestions() -> None:
    from app.core.documents.engine import render_docx
    from app.modules.lifecycle.stocktake_record import (
        STOCKTAKE_RECORD,
        StocktakeRecordData,
        stocktake_context,
    )

    data = {
        **STOCKTAKE_RECORD.sample_data,
        "totals": {**STOCKTAKE_RECORD.sample_data["totals"], "damaged_light": 17},
    }
    ctx = stocktake_context(StocktakeRecordData.model_validate(data), "KK-1")
    text = docx_text(render_docx(STOCKTAKE_RECORD.default_template.read_bytes(), ctx))
    assert "Hư hỏng nhẹ" in text and "17" in text and "Hỏng, không dùng được" in text

    item = {
        "id": 1,
        "name": "x",
        "count": "missing",
        "resolution": "investigate",
        "resolution_suggested": True,
    }
    head = {"name": "k", "cutoff": "2026-12-31", "status_label": "Đang kiểm"}
    for suggested, expect in ((True, "Tìm thêm (ghi rõ) (gợi ý)"), (False, "Tìm thêm (ghi rõ)")):
        wb = load_workbook(io.BytesIO(to_xlsx(head, [{**item, "resolution_suggested": suggested}])))
        assert next(wb["Chênh lệch"].iter_rows(min_row=2, values_only=True))[17] == expect


def test_a_resolution_outside_the_kind_still_counts_as_unresolved() -> None:
    assert summary([{"count": "missing", "resolution": "repair"}])["unresolved"] == 1
    assert summary([{"count": "missing", "resolution": "dispose"}])["unresolved"] == 0


async def test_migration_0010_data_steps(admin) -> None:  # type: ignore[no-untyped-def]
    """The SQL of 0010 on prepared data: open stocktakes move to rules 2 and lose orphaned light-damage
    resolutions, closed ones keep rules 1, supervisors get the assign grant."""
    import importlib.util
    from pathlib import Path

    from sqlalchemy import text

    from app.core.db import get_sessionmaker
    from app.modules.lifecycle.models import Stocktake, StocktakeItem

    path = Path(__file__).parents[1] / "migrations/lifecycle/versions/0010_lifecycle_stocktake_simplify.py"
    spec = importlib.util.spec_from_file_location("mig0010", path)
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    sql: list[str] = []

    class Recorder:  # only the data statements: the schema already is at heads
        def execute(self, stmt: str) -> None:
            sql.append(stmt)

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return lambda *a, **k: None

    mig.op = Recorder()
    mig.upgrade()
    assert len(sql) == 3

    async with get_sessionmaker()() as s:
        ids = {}
        for status in ("counting", "closed"):
            st = Stocktake(
                name=f"mig-{status}",
                cutoff=date(2026, 12, 31),
                status=status,
                created_by="t",
                rules_version=1,
            )
            s.add(st)
            await s.flush()
            ids[status] = st.id
            s.add(
                StocktakeItem(
                    stocktake_id=st.id, name="n", count="found", condition="damaged",
                    resolution="repair", resolution_note="x", resolved_by="t",
                )
            )  # fmt: skip
        await s.flush()
        grant = "SELECT count(*) FROM core_role_permission WHERE permission_code = 'lifecycle.stocktake.assign' AND role_id = (SELECT id FROM core_role WHERE code = 'supervisor')"
        await s.execute(text(grant.replace("SELECT count(*)", "DELETE").replace("= (SELECT", "IN (SELECT")))
        assert await s.scalar(text(grant)) == 0
        for stmt in sql:
            await s.execute(text(stmt))
        assert await s.scalar(text(grant)) == 1
        await s.commit()
        for status, kept in (("counting", None), ("closed", "repair")):
            item = await s.scalar(select(StocktakeItem).where(StocktakeItem.stocktake_id == ids[status]))
            assert item.resolution == kept
        rv = {
            r.id: r.rules_version
            for r in await s.scalars(select(Stocktake).where(Stocktake.id.in_(ids.values())))
        }
        assert rv == {ids["counting"]: 2, ids["closed"]: 1}
        for sid in ids.values():
            await s.delete(await s.get(Stocktake, sid))
        await s.commit()


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_operators_touch_only_their_zones_and_extras_carry_a_zone(  # type: ignore[no-untyped-def]
    admin,
    client,
    configured,  # noqa: F811
    respx_mock,
) -> None:
    mock_systems(respx_mock)
    await _sync()
    areas = "/api/lifecycle/stocktake-areas"
    await admin.post(areas, json={"name": "Tầng 5", "departments": ["Kế Toán"], "position": 1})
    await admin.post(areas, json={"name": "Kho IT", "is_store": True, "position": 9})
    base, _ = await _open_stocktake(admin, "Khu")
    st = (await admin.get(base)).json()
    zones = {z["name"]: z for z in st["zones"]}
    by_asset = {i["asset_id"]: i for i in st["items"]}
    a1, a2 = by_asset["a1"], by_asset["a2"]  # a1 in Tầng 5, a2 in Kho IT
    assert a1["zone_id"] == zones["Tầng 5"]["id"] and a2["zone_id"] == zones["Kho IT"]["id"]
    # K-14: managers are told how many assets sit in no zone (the operator's view has no such number).
    assert st["summary"]["unzoned"] == sum(1 for i in st["items"] if not i["zone_id"])

    await _user(admin, "operator", "st-op4@test.local")
    op_id = next(
        c["id"]
        for c in (await admin.get("/api/lifecycle/stocktake-counters")).json()
        if c["upn"] == "st-op4@test.local"
    )
    await admin.put(f"{base}/zones/{zones['Kho IT']['id']}", json={"user_id": op_id})
    await admin.post(f"{base}/scan", json={"code": "LT0123"})  # a1 counted by the manager (Tầng 5)
    await client.login("st-op4@test.local")
    assert "unzoned" not in (await client.get(base)).json()["summary"]

    # K-13: no counting / extra removal outside my zone; a scan must name my zone.
    other = await client.put(f"{base}/items/{a1['id']}", json={"count": "missing"})
    assert other.status_code == 403
    assert (await client.put(f"{base}/items/{a2['id']}", json={"count": "found"})).status_code == 200
    assert (await client.post(f"{base}/scan", json={"code": "SN2"})).status_code == 403  # no zone_id
    mine = zones["Kho IT"]["id"]
    seen = await client.post(f"{base}/scan", json={"code": "LT0123", "zone_id": mine})
    assert seen.status_code == 200 and seen.json() == {"matched": True, "item": None, "already": True}

    # K-14: an extra carries a zone (required for an Operator, checked like a scan) and stays in their view.
    body = {"name": "Lạ", "serial": "X-OP"}
    assert (await client.post(f"{base}/extras", json=body)).status_code == 403
    foreign = await client.post(f"{base}/extras", json={**body, "zone_id": zones["Tầng 5"]["id"]})
    assert foreign.status_code == 403
    ok = await client.post(f"{base}/extras", json={**body, "zone_id": mine})
    assert ok.status_code == 201 and ok.json()["zone_id"] == mine
    assert any(i["id"] == ok.json()["id"] for i in (await client.get(base)).json()["items"])
    # A duplicate code of an asset in another zone does not reveal it.
    dup = await client.post(f"{base}/extras", json={"name": "Trùng", "serial": "SN1", "zone_id": mine})
    assert dup.json()["error"]["code"] == "extra_duplicate" and "LT0123" not in dup.json()["error"]["message"]
    # Extras of other zones cannot be removed by an Operator.
    mgr_extra = (await admin.post(f"{base}/extras", json={"name": "Của KT", "serial": "X-MG"})).json()
    assert (await client.delete(f"{base}/extras/{mgr_extra['id']}")).status_code == 403
    assert (await client.delete(f"{base}/extras/{ok.json()['id']}")).status_code == 204
    assert (await admin.delete(f"{base}/extras/{mgr_extra['id']}")).status_code == 204
    for a in (await admin.get(areas)).json()["areas"]:
        await admin.delete(f"{areas}/{a['id']}")


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_a_changed_difference_kind_clears_the_old_resolution(  # type: ignore[no-untyped-def]
    admin,
    client,
    configured,  # noqa: F811
    respx_mock,
) -> None:
    mock_systems(respx_mock)
    await _sync()
    base, _ = await _open_stocktake(admin, "Đổi loại")
    items = (await admin.get(base)).json()["items"]
    a, b, c = items[0], items[1], items[2]
    for i in (a, b, c):
        await admin.put(f"{base}/items/{i['id']}", json={"count": "missing"})
    await admin.post(f"{base}/finish-count")  # reviewing: missing → found must still be possible
    for i in (a, b, c):
        r = await admin.put(f"{base}/items/{i['id']}/resolution", json={"resolution": "dispose"})
        assert r.json()["resolution"] == "dispose"
    # set_count: missing → found (broken) changes the kind, so "dispose" (valid for both) must still be dropped.
    r = await admin.put(f"{base}/items/{a['id']}", json={"count": "found", "condition": "broken"})
    assert r.json()["difference"] and r.json()["resolution"] is None
    # missing → found, good: no difference at all.
    r = await admin.put(f"{base}/items/{b['id']}", json={"count": "found", "condition": "good"})
    assert r.json()["difference"] is None and r.json()["resolution"] is None
    # Same kind keeps it (missing → missing with a note).
    r = await admin.put(f"{base}/items/{c['id']}", json={"count": "missing", "note": "n"})
    assert r.json()["resolution"] == "dispose"
    # The summary treats a resolution not allowed for the kind as unresolved.
    assert (await admin.get(base)).json()["summary"]["unresolved"] >= 1


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_batch_clears_a_stale_resolution_when_the_kind_changes(  # type: ignore[no-untyped-def]
    admin,
    client,
    configured,  # noqa: F811
    respx_mock,
) -> None:
    from sqlalchemy import update

    from app.core.db import get_sessionmaker
    from app.modules.lifecycle.models import StocktakeItem

    mock_systems(respx_mock)
    await _sync()
    base, _ = await _open_stocktake(admin, "Lô xoá")
    item = (await admin.get(base)).json()["items"][0]
    async with get_sessionmaker()() as s:  # a stale handling left on a pending asset
        await s.execute(
            update(StocktakeItem)
            .where(StocktakeItem.id == item["id"])
            .values(resolution="dispose", resolved_by="t")
        )
        await s.commit()
    r = await admin.post(
        f"{base}/count-batch", json={"item_ids": [item["id"]], "count": "found", "condition": "broken"}
    )
    out = r.json()["items"][0]
    assert out["difference"] and out["resolution"] is None and out["resolved_by"] is None
