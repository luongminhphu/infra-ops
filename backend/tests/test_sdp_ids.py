"""SDP users missing an employee ID: list + Excel export over a stored run."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from openpyxl import load_workbook
from sqlalchemy import delete, select

from app.core.audit.models import AuditLog
from app.core.db import get_sessionmaker
from app.modules.lifecycle.models import Snapshot, SyncRun

URL = "/api/lifecycle/sdp-missing-ids"


@pytest.fixture(autouse=True)
async def _drop_fake_runs():  # type: ignore[no-untyped-def]
    """The fake runs are dated far ahead so they win latest_run; remove them so other tests are unaffected."""
    yield
    async with get_sessionmaker()() as s:
        await s.execute(delete(SyncRun).where(SyncRun.trigger == "sdp-ids-test"))
        await s.commit()


async def _run(errors: dict[str, str] | None = None) -> None:
    async with get_sessionmaker()() as s:
        run = SyncRun(
            trigger="sdp-ids-test",
            status="partial" if errors else "success",
            stats={},
            errors=errors or {},
            notes=[],
            finished_at=datetime.now(UTC),
            started_at=datetime.now(UTC) + timedelta(days=36500),  # newest run wins
        )
        s.add(run)
        await s.flush()
        sdp = [
            {"id": "1", "name": "Nguyễn Văn An", "email": "an@x.vn", "employee_id": None, "department": "IT"},
            {"id": "2", "name": "Không Ai", "email": None, "employee_id": "N/A", "department": None},
            {"id": "3", "name": "Có Mã", "email": None, "employee_id": "000777", "department": None},
        ]
        bs = [{"user_id": "003551", "name": "NGUYEN VAN AN", "source": "BS", "active": True, "ref": "003551"}]
        for system, items, key in (("sdp_user", sdp, "id"), ("biostar_user", bs, "ref")):
            s.add_all(Snapshot(run_id=run.id, system=system, external_id=i[key], data=i) for i in items)
        await s.commit()


async def test_list_and_xlsx(admin) -> None:  # type: ignore[no-untyped-def]
    await _run()
    body = (await admin.get(URL)).json()
    assert body["sources"] == {"sdp": "ok", "biostar": "ok", "failed_systems": []}
    assert body["total_missing"] == 2 and body["counts"] == {"matched": 1, "none": 1}
    (item,) = body["items"]  # only_candidates defaults to true
    assert item["suggestion"]["employee_id"] == "03551" and item["status"] == "matched"
    assert set(item) == {
        "sdp_user_id", "name", "email", "department", "current_value", "status", "suggestion", "candidates"
    }  # fmt: skip
    assert len((await admin.get(URL, params={"only_candidates": "false"})).json()["items"]) == 2

    resp = await admin.get(URL + ".xlsx")
    assert resp.status_code == 200
    ws = load_workbook(io.BytesIO(resp.content)).active
    assert ws.title == "User SDP thiếu Mã NV"
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == (
        "Tên SDP", "Email", "Phòng ban", "Giá trị hiện tại", "Trạng thái", "Mã NV đề xuất (5 số cuối)",
        "Mã trên BioStar", "Tên BioStar", "Hệ thống BioStar", "Mức khớp", "Ghi chú", "Ứng viên khác",
    )  # fmt: skip
    assert rows[1][4] == "Khớp tên" and rows[1][5] == "03551" and rows[1][6] == "003551"
    async with get_sessionmaker()() as s:
        n = await s.scalar(select(AuditLog.id).where(AuditLog.action == "lifecycle.sdp_missing_ids_exported"))
    assert n is not None


async def test_source_failures_are_not_clean_results(admin) -> None:  # type: ignore[no-untyped-def]
    await _run({"sdp_users": "boom"})
    body = (await admin.get(URL)).json()
    assert body["items"] == [] and body["sources"]["sdp"] == "failed"
    await _run({"biostar": "boom"})
    body = (await admin.get(URL, params={"only_candidates": "false"})).json()
    assert body["sources"]["biostar"] == "failed" and body["sources"]["failed_systems"] == ["biostar"]
    assert body["items"] and all(i["suggestion"] is None and not i["candidates"] for i in body["items"])
    # default only_candidates must not hide rows when BioStar failed (list and xlsx)
    body = (await admin.get(URL)).json()
    assert [i["status"] for i in body["items"]] == ["unknown", "unknown"]
    ws = load_workbook(io.BytesIO((await admin.get(URL + ".xlsx")).content)).active
    assert ws.max_row == 3
    await _run({"sdp_assets": "boom"})
    assert (await admin.get(URL)).json()["sources"]["sdp"] == "ok"


def test_xlsx_other_candidates_and_ambiguous_notes() -> None:
    from app.modules.lifecycle.id_suggest import suggest
    from app.modules.lifecycle.sdp_ids_api import to_xlsx

    bs = [
        {"user_id": "1001", "name": "An Nguyen", "source": "BS", "active": True},
        {"user_id": "1002", "name": "An Nguyen", "source": "BS", "active": True},
        {"user_id": "1003", "name": "An Nguyen", "source": "BS", "active": False},
    ]
    rows = suggest([{"id": "1", "name": "Nguyen An", "employee_id": None}], bs)
    cells = list(load_workbook(io.BytesIO(to_xlsx(rows))).active.iter_rows(values_only=True))[1]
    assert rows[0]["status"] == "ambiguous" and "1003" in cells[11] and "đã khoá" in cells[11]
    twins = suggest(
        [{"id": "1", "name": "Nguyen An"}, {"id": "2", "name": "An Nguyen"}], bs[:1]
    )  # same top candidate for two SDP users
    cells = list(load_workbook(io.BytesIO(to_xlsx(twins))).active.iter_rows(values_only=True))[1]
    assert "Cùng ứng viên với user SDP" in cells[10]


async def test_requires_reconciliation_view(client) -> None:  # type: ignore[no-untyped-def]
    await client.login("viewer-lc@test.local")
    assert (await client.get(URL)).status_code == 403
    assert (await client.get(URL + ".xlsx")).status_code == 403
