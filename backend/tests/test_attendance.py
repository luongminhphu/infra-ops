"""Attendance report: BioStar access logs by employee ID → Excel."""

from __future__ import annotations

import io
import json
from datetime import date

import httpx
import respx
from openpyxl import load_workbook

from app.modules.lifecycle.attendance import (
    Report,
    add_events,
    daily_rows,
    is_success,
    match_users,
    parse_ids,
)
from tests.test_lifecycle_api import BS, configured  # noqa: F401  (fixture)


def ev(user: str, at: str, code: str = "4867", device: str = "Cửa chính") -> dict:  # type: ignore[type-arg]
    return {
        "datetime": at,
        "user_id": {"user_id": user, "name": f"User {user}"},
        "device_id": {"id": "1", "name": device},
        "event_type_id": {"code": code},
    }


def test_daily_first_and_last_scan_in_vietnam_time() -> None:
    assert parse_ids("003551, 003475\n003551;  001163 ") == ["003551", "003475", "001163"]
    assert is_success(ev("1", "x", "4865")) and is_success(ev("1", "x", "4102"))
    assert not is_success(ev("1", "x", "5124")) and not is_success(ev("1", "x", "4354"))  # failed scans
    # Office BioStar keeps a 7-digit ID: matched on the last 5 digits.
    by_user = match_users(["003551", "003475"], [{"user_id": "1003551"}, {"user_id": "000001"}])
    assert by_user == {"1003551": "003551"}

    r = Report(["003551", "003475"], date(2026, 11, 2), date(2026, 11, 3), not_found=["003475"])
    add_events(
        r,
        [
            ev("1003551", "2026-11-02T01:05:00.00Z"),  # 08:05
            ev("1003551", "2026-11-02T10:40:00.00Z"),  # 17:40
            ev("1003551", "2026-11-02T03:00:00.00Z", code="5124"),  # failed: ignored
            ev("1003551", "2026-11-02T18:30:00.00Z"),  # 01:30 on the 3rd
            ev("000001", "2026-11-02T02:00:00.00Z"),  # not requested
        ],
        by_user,
        "BS Office",
    )
    rows = daily_rows(r)
    assert rows[0][2:8] == [date(2026, 11, 2), "Thứ 2", "08:05", "17:40", 9.58, 2]
    assert rows[1][2:9] == [date(2026, 11, 3), "Thứ 3", "01:30", "", None, 1, ""]
    assert len(rows) == 2  # 003475 not on BioStar: only listed in the notes


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_export_reads_biostar_and_is_audited(admin, client, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    respx_mock.post(f"{BS}/api/login").mock(
        return_value=httpx.Response(200, headers={"bs-session-id": "s"}, json={"User": {}})
    )
    respx_mock.post(f"{BS}/api/logout").mock(return_value=httpx.Response(200, json={}))
    respx_mock.get(f"{BS}/api/users").mock(
        return_value=httpx.Response(
            200,
            json={
                "UserCollection": {
                    "total": "2",
                    "rows": [
                        {"user_id": "003551", "name": "Nguyễn Văn An"},
                        {"user_id": "009999", "name": "Khác"},
                    ],
                }
            },
        )
    )
    search = respx_mock.post(f"{BS}/api/events/search").mock(
        return_value=httpx.Response(
            200, json={"EventCollection": {"rows": [ev("003551", "2026-11-02T01:00:00.00Z")]}}
        )
    )
    body = {"employee_ids": "003551\n003475", "date_from": "2026-11-02", "date_to": "2026-11-03"}
    resp = await admin.post("/api/lifecycle/attendance/export", json=body)
    assert resp.status_code == 200, resp.text
    assert (
        resp.headers["x-not-found"] == "1"
        and "cham-cong-20261102-20261103" in resp.headers["content-disposition"]
    )
    query = json.loads(search.calls[0].request.content)["Query"]
    assert query["conditions"][1]["values"] == ["003551"]  # only matched users are searched
    assert query["conditions"][0]["values"] == ["2026-11-01T17:00:00.000Z", "2026-11-03T17:00:00.000Z"]
    wb = load_workbook(io.BytesIO(resp.content))
    daily = list(wb["Tổng hợp theo ngày"].iter_rows(min_row=2, values_only=True))
    assert [(r[0], r[1], r[4]) for r in daily] == [
        ("003551", "Nguyễn Văn An", "08:00"),
        ("003551", "Nguyễn Văn An", None),
    ]
    assert any("003475" in (r[0] or "") for r in wb["Ghi chú"].iter_rows(values_only=True))
    audit = (await admin.get("/api/core/audit", params={"action": "lifecycle.attendance_exported"})).json()
    assert audit["items"] and "2 Mã NV" in audit["items"][0]["message"]

    too_long = {**body, "date_to": "2027-02-01"}
    assert (await admin.post("/api/lifecycle/attendance/export", json=too_long)).status_code == 400
    await client.login("att-viewer@test.local")  # first login = Viewer: no attendance data
    assert (await client.post("/api/lifecycle/attendance/export", json=body)).status_code == 403
