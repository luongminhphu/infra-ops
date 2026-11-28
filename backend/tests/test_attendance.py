"""Attendance report: BioStar access logs by employee ID → Excel."""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, date, datetime
from urllib.parse import unquote

import httpx
import respx
from openpyxl import load_workbook

from app.modules.lifecycle import attendance_api
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
                        {"user_id": "1003551", "name": "Nguyễn Văn An"},  # BioStar Office: prefix 1
                        {"user_id": "009999", "name": "Khác"},
                    ],
                }
            },
        )
    )
    search = respx_mock.post(f"{BS}/api/events/search").mock(
        return_value=httpx.Response(
            200, json={"EventCollection": {"rows": [ev("1003551", "2026-11-02T01:00:00.00Z")]}}
        )
    )
    body = {"employee_ids": "003551\n003475", "date_from": "2026-11-02", "date_to": "2026-11-03"}
    resp = await admin.post("/api/lifecycle/attendance/export", json=body)
    assert resp.status_code == 200, resp.text
    assert (
        resp.headers["x-not-found"] == "1"
        and "cham-cong-20261102-20261103" in resp.headers["content-disposition"]
    )
    assert (resp.headers["x-requested"], resp.headers["x-not-found-ids"]) == ("2", "003475")
    assert (
        resp.headers["x-read-ok"],
        resp.headers["x-read-errors"],
        resp.headers["x-read-error-detail"],
    ) == ("1", "0", "")
    # Matched on the last 5 digits; the result panel shows it per system.
    detail = unquote(resp.headers["x-source-detail"])
    assert "1/2 Mã NV khớp theo 5 số cuối (vd. 003551↔1003551), 1 lượt quẹt" in detail, detail
    assert (await admin.get("/api/lifecycle/attendance/status")).json() == {"ready": True, "message": None}
    query = json.loads(search.calls[0].request.content)["Query"]
    assert query["conditions"][1]["values"] == ["1003551"]  # only matched users, by their BioStar ID
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


def test_employee_list_from_email_table_excel_and_plain_text() -> None:
    from app.modules.lifecycle.attendance_input import extract, rows_from_text

    # Table copied from an e-mail: tab-separated, header row, Excel dropped the leading zeros.
    table = "STT\tMã NV\tHọ và tên\tPhòng ban\n1\t3551\tNguyễn Văn An\tIT\n2\t003475\tTrần Thị B\tHR\n3\t\tThiếu mã\tIT"
    p = extract(rows_from_text(table))
    assert p.header == "Mã NV" and p.skipped == 1
    assert p.items == [
        {"employee_id": "003551", "name": "Nguyễn Văn An"},
        {"employee_id": "003475", "name": "Trần Thị B"},
    ]
    # No header: every 4–8 digit cell; row numbers, dates and phone numbers are not IDs.
    loose = "Nhờ IT kiểm tra:\n1. Nguyễn A - 003551 (OT 15/11)\n2. 003612 Lê B, 0901234567"
    assert extract(rows_from_text(loose)).ids == ["003551", "003612"]
    assert extract(rows_from_text("003551, 003475\n003551;  001163")).ids == ["003551", "003475", "001163"]


async def test_parse_endpoint_reads_xlsx_and_pasted_text(admin) -> None:  # type: ignore[no-untyped-def]
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Danh sách rà soát OT tháng 11"])
    ws.append(["Mã số nhân viên", "Họ tên"])
    ws.append([3551, "Nguyễn Văn An"])
    ws.append(["003475", "Trần Thị B"])
    buf = io.BytesIO()
    wb.save(buf)
    files = {
        "file": (
            "ot.xlsx",
            buf.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    r = await admin.post("/api/lifecycle/attendance/parse", files=files)
    assert r.status_code == 200, r.text
    got = r.json()
    assert [i["employee_id"] for i in got["items"]] == ["003551", "003475"] and got[
        "column"
    ] == "Mã số nhân viên"
    r = await admin.post("/api/lifecycle/attendance/parse", data={"text": "không có mã nào"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_employee_ids"
    old = {"file": ("ot.xls", b"\xd0\xcf\x11\xe0", "application/vnd.ms-excel")}
    assert (await admin.post("/api/lifecycle/attendance/parse", files=old)).json()["error"][
        "code"
    ] == "unsupported_file"


async def test_status_and_unreadable_biostar_explain_the_problem(admin, client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A BioStar profile that does not exist (other tests may have configured a real one in this database).
    monkeypatch.setattr(attendance_api, "main_biostar", lambda _s: {"profile_id": uuid.uuid4()})
    status = (await admin.get("/api/lifecycle/attendance/status")).json()
    assert status["ready"] is False and "biostar" in status["message"]
    body = {"employee_ids": "003551", "date_from": "2026-11-02", "date_to": "2026-11-03"}
    resp = await admin.post("/api/lifecycle/attendance/export", json=body)
    err = resp.json()["error"]
    assert err["code"] == "biostar_unreadable" and err["message"].startswith(
        "Không đọc được BioStar: BioStar chính: "
    )
    await client.login("att-status-viewer@test.local")  # Viewer: may not check either
    assert (await client.get("/api/lifecycle/attendance/status")).status_code == 403


async def test_unreachable_biostar_is_reported_without_raw_exception(admin, configured, respx_mock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    respx_mock.post(f"{BS}/api/login").mock(side_effect=httpx.ConnectError("boom: secret-host:1234"))
    body = {"employee_ids": "003551", "date_from": "2026-11-02", "date_to": "2026-11-03"}
    err = (await admin.post("/api/lifecycle/attendance/export", json=body)).json()["error"]
    assert err["code"] == "biostar_unreadable" and "boom" not in err["message"]


async def _seed_run(errors: dict, year: int, extra_sdp: tuple | None = None) -> None:  # type: ignore[type-arg]
    from app.core.db import get_sessionmaker
    from app.modules.lifecycle.models import Snapshot, SyncRun

    at = datetime(year, 1, 1, tzinfo=UTC)
    async with get_sessionmaker()() as s:
        run = SyncRun(
            trigger="manual", status="partial", started_at=at, finished_at=at, stats={}, errors=errors
        )
        s.add(run)
        await s.flush()
        for sid, emp, name in [("1", "003551", "Nguyễn Văn An"), ("2", "004000", "Trần Thị Bình"),
                               ("3", "005000", "Lê Cường"), ("4", "006000", "Phạm Dũng")]:  # fmt: skip
            s.add(Snapshot(run_id=run.id, system="sdp_user", external_id=sid, employee_id=emp,
                           data={"id": sid, "employee_id": emp, "name": name}))  # fmt: skip
        if extra_sdp:
            sid, emp, name = extra_sdp
            s.add(Snapshot(run_id=run.id, system="sdp_user", external_id=sid, employee_id=emp,
                           data={"id": sid, "employee_id": emp, "name": name}))  # fmt: skip
        for uid, name in [("1003551", "NGUYEN VAN AN"), ("004000", "Someone Else"), ("007000", "Hoàng Em")]:
            s.add(Snapshot(run_id=run.id, system="biostar_user", external_id=uid,
                           data={"user_id": uid, "name": name, "source": "BioStar chính", "active": True}))  # fmt: skip
        await s.commit()


async def test_preview_statuses_last_five_digits_and_failed_source(admin, client) -> None:  # type: ignore[no-untyped-def]
    try:
        await _preview_checks(admin, client)
    finally:  # the runs are the newest ones: other tests must not see them (snapshots cascade)
        from sqlalchemy import delete

        from app.core.db import get_sessionmaker
        from app.modules.lifecycle.models import SyncRun

        async with get_sessionmaker()() as s:
            await s.execute(delete(SyncRun).where(SyncRun.started_at >= datetime(2100, 1, 1, tzinfo=UTC)))
            await s.commit()


async def _preview_checks(admin, client) -> None:  # type: ignore[no-untyped-def]
    await _seed_run({}, 2100)
    ids = "3551\n004000\n005000\n007000\n009999"
    r = await admin.post("/api/lifecycle/attendance/preview", json={"employee_ids": ids})
    assert r.status_code == 200, r.text
    got = {i["employee_id"]: i for i in r.json()["items"]}
    assert {k: v["status"] for k, v in got.items()} == {
        "003551": "ok", "004000": "name_differs", "005000": "sdp_only",
        "007000": "biostar_only", "009999": "not_found",
    }  # fmt: skip
    assert got["003551"]["sdp_name"] == "Nguyễn Văn An"
    assert got["003551"]["biostar"] == [
        {"name": "NGUYEN VAN AN", "user_id": "1003551", "source": "BioStar chính", "active": True}
    ]
    assert r.json()["synced_at"] and r.json()["failed_sources"] == []

    await _seed_run({"biostar": "boom"}, 2101)
    r = await admin.post("/api/lifecycle/attendance/preview", json={"employee_ids": "005000\n009999\n3551"})
    assert r.json()["failed_sources"] == ["biostar"]
    assert {i["employee_id"]: i["status"] for i in r.json()["items"]} == {
        "005000": "unknown", "009999": "unknown", "003551": "ok",
    }  # fmt: skip

    await _seed_run({"sdp_assets": "boom"}, 2102)  # an unrelated SDP read does not make the result unknown
    r = await admin.post("/api/lifecycle/attendance/preview", json={"employee_ids": "005000\n009999"})
    assert {i["employee_id"]: i["status"] for i in r.json()["items"]} == {
        "005000": "sdp_only", "009999": "not_found",
    }  # fmt: skip
    await _seed_run({}, 2103, extra_sdp=("9", "103551", "Người Khác"))  # same 5 digits, another name
    r = await admin.post("/api/lifecycle/attendance/preview", json={"employee_ids": "3551"})
    assert r.json()["items"][0]["status"] == "ok"

    await client.login("att-viewer@test.local")
    resp = await client.post("/api/lifecycle/attendance/preview", json={"employee_ids": ids})
    assert resp.status_code == 403
