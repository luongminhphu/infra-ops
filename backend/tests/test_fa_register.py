"""Accounting registers (sổ TSCĐ / CCDC): parser, finance-only API, matching."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.core.audit.models import AuditLog
from app.core.db import get_sessionmaker
from app.core.errors import AppError
from app.modules.lifecycle.fa_register import (
    MAX_ROWS,
    match_register,
    norm_code,
    parse_number,
    parse_register,
    reconcile_registers,
    register_totals,
)
from app.modules.lifecycle.models import Stocktake, StocktakeItem
from tests.test_workload import _user


def xlsx(rows: list[list[object]], title: str = "Sổ") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def tok(b: bytes, mapping: str | None = None, sheet: str = "Sổ") -> str:
    """The preview_token the API computes: sha256(file | sheet | canonical mapping)."""
    canon = json.dumps(json.loads(mapping) if mapping else {}, sort_keys=True)
    return hashlib.sha256(f"{hashlib.sha256(b).hexdigest()}|{sheet}|{canon}".encode()).hexdigest()


HEAD2 = [
    ["Mã TSCĐ", "Tên tài sản", "Nguyên giá", "Hao mòn", "Giá trị còn lại", "Ngày ghi tăng", "Serial"],
    [None, None, None, "Luỹ kế", None, None, None],
]


def test_numbers_follow_the_vietnamese_rule() -> None:
    d = Decimal
    assert parse_number("1.234.567") == d(1234567)
    assert parse_number("1,234,567.50") == d("1234567.50")
    assert parse_number("1.234.567,5") == d("1234567.5")
    assert parse_number("(1.234.567)") == d(-1234567)
    assert parse_number("-2.500") == d(-2500)
    assert parse_number("12,5") == d("12.5") and parse_number("1.5") == d("1.5")
    assert parse_number(12.1) == d("12.1") and parse_number(None) is None and parse_number("  ") is None
    with pytest.raises(ValueError):
        parse_number("abc")


def test_two_row_header_total_row_dates_and_duplicates() -> None:
    data = xlsx(
        [
            ["CÔNG TY ABC"],
            *HEAD2,
            ["'ts001", "Laptop Dell", "30.000.000", 10000000, "(1.000)", "05/03/2024", "SN-1"],
            ["TS001", "Laptop trùng", 5, 0, 0, datetime(2024, 3, 6), ""],
            ["TS002", "Công cụ dụng cụ khác", Decimal("1.5"), None, None, "không phải ngày", None],
            [None, None, None, None, None, None, None],
            ["Cộng", None, 99, None, None, None, None],
            [None, "Tổng cộng", 99, None, None, None, None],
        ]
    )
    p = parse_register(data)
    assert p.header_rows == 2 and p.columns["accumulated"] == "Hao mòn – Luỹ kế"
    assert [r.code_norm for r in p.rows] == ["TS001", "TS001", "TS002"]  # totals dropped, duplicates kept
    a, b, c = p.rows
    assert (a.cost, a.accumulated, a.net, a.serial) == (
        Decimal(30000000),
        Decimal(10000000),
        Decimal(-1000),
        "SN-1",
    )
    assert a.in_use_date == date(2024, 3, 5) and b.in_use_date == date(2024, 3, 6) and c.in_use_date is None
    assert c.name.startswith("Công cụ") and c.cost == Decimal("1.5")
    assert any("trùng mã" in w for w in p.warnings)
    assert p.errors == [{"row": 6, "column": "Ngày ghi tăng", "value": "không phải ngày"}]
    assert p.skipped == {"total": 2}
    assert p.total_cost == Decimal("30000006.5")


def test_aliases_match_by_equality_not_substring() -> None:
    # "Nguyên giá tăng thêm" must not become the cost column; "Mã số thuế" is not the code.
    p = parse_register(
        xlsx(
            [
                ["Mã số", "Tên CCDC", "Nguyên giá tăng thêm", "Nguyên giá", "Mã số thuế"],
                ["C1", "Bàn", 1, 2, 3],
            ]
        )
    )
    assert p.columns["cost"] == "Nguyên giá" and p.rows[0].cost == Decimal(2) and p.columns["code"] == "Mã số"


def test_refuses_non_xlsx_oversize_missing_header_and_too_many_rows() -> None:
    for bad in (b"MZ not a workbook", b""):
        with pytest.raises(AppError) as e:
            parse_register(bad)
        assert e.value.code == "unsupported_file"
    with pytest.raises(AppError) as e:
        parse_register(b"PK\x03\x04" + b"0" * 10)
    assert e.value.code == "xlsx_unreadable"
    with pytest.raises(AppError) as e:
        parse_register(xlsx([["a", "b"], [1, 2]]))
    assert e.value.code == "register_header_not_found"
    with pytest.raises(AppError) as e:
        parse_register(xlsx([["Mã TSCĐ", "Tên tài sản"], *[[f"T{i}", "x"] for i in range(MAX_ROWS + 1)]]))
    assert e.value.code == "register_too_long"
    with pytest.raises(AppError) as e:
        parse_register(xlsx([["Mã TSCĐ", "Tên tài sản"], ["T1", "x"]]), sheet="none")
    assert e.value.code == "sheet_not_found"


def test_matching_by_normalised_code_with_serial_fallback() -> None:
    rows = [
        {"code_norm": "TS1", "serial": ""},
        {"code_norm": "TS1", "serial": ""},
        {"code_norm": "TS2", "serial": "sn-222"},
        {"code_norm": "TS3", "serial": "sn-333"},
    ]
    items = [
        {"id": 1, "fa_code": " 'ts1 ", "serial": "SN-333"},
        {"id": 2, "fa_code": None, "serial": "SN-222"},  # no code → by serial
        {"id": 3, "fa_code": "TS99", "serial": "SN-333"},  # code not in the register → serial fallback
    ]
    out = {m["code_norm"]: m for m in match_register(rows, items, "tscd")}
    assert len(out["TS1"]["rows"]) == 2 and [i["id"] for i in out["TS1"]["items"]] == [1]
    assert [i["id"] for i in out["TS2"]["items"]] == [2] and out["TS2"]["kind"] == "tscd"
    # item 1 has a code match, so its serial is ignored
    assert [i["id"] for i in out["TS3"]["items"]] == [3] and norm_code("'a-1 ") == "A-1"


async def _stocktake(status: str = "counting") -> tuple[str, int]:
    async with get_sessionmaker()() as s:
        st = Stocktake(name="KK", cutoff=date(2026, 12, 31), status=status, created_by="t@test.local")
        s.add(st)
        await s.flush()
        it = StocktakeItem(stocktake_id=st.id, name="LT", fa_code="TS001")
        s.add(it)
        await s.commit()
        return str(st.id), it.id


async def test_register_api_is_finance_only_audited_and_replaces(admin, client) -> None:  # type: ignore[no-untyped-def]
    sid, item_id = await _stocktake()
    base = f"/api/lifecycle/stocktakes/{sid}"
    f1 = xlsx(
        [
            HEAD2[0],
            ["TS001", "Laptop", "30.000.000", 1, 2, "01/01/2024", ""],
            ["TS002", "Chuột", 100, 0, 0, None, ""],
        ]
    )
    f2 = xlsx([HEAD2[0], ["TS009", "Màn hình", "7.000.000", 0, 0, None, ""]])
    xl = lambda b: {"file": ("so.xlsx", b, "application/octet-stream")}  # noqa: E731
    ok_ = lambda b: {"confirm": "true", "preview_token": tok(b)}  # noqa: E731

    for role in ("operator", "viewer"):
        await _user(admin, role, f"fa-{role}@test.local")
        await client.login(f"fa-{role}@test.local")
        assert (await client.post(f"{base}/registers/tscd/preview", files=xl(f1))).status_code == 403
        assert (await client.get(f"{base}/registers")).status_code == 403
        assert (await client.delete(f"{base}/registers/tscd")).status_code == 403

    prev = await admin.post(f"{base}/registers/tscd/preview", files=xl(f1))
    assert prev.status_code == 200, prev.text
    assert (
        prev.json()["rows"] == 2
        and prev.json()["total_cost"] == "30000100"
        and len(prev.json()["first_rows"]) == 2
    )
    assert (await admin.get(f"{base}/registers")).json()["total"] == 0  # preview stores nothing
    assert prev.json()["sha256"] == hashlib.sha256(f1).hexdigest() and prev.json()["errors"] == []
    assert (await admin.post(f"{base}/registers/tscd", files=xl(f1), data={"preview_token": "x"})).json()[
        "error"
    ]["code"] == "confirm_required"
    bad = await admin.post(f"{base}/registers/tscd/preview", files={"file": ("a.xlsx", b"%PDF-1.4", "x/y")})
    assert bad.json()["error"]["code"] == "unsupported_file"

    ok = await admin.post(f"{base}/registers/tscd", files=xl(f1), data=ok_(f1))
    assert ok.status_code == 200 and ok.json()["rows"] == 2
    again = await admin.post(f"{base}/registers/tscd", files=xl(f2), data=ok_(f2))
    assert again.json()["total_cost"] == "7000000"
    got = (await admin.get(f"{base}/registers")).json()
    assert [r["code"] for r in got["rows"]] == ["TS009"] and got["meta"]["tscd"]["rows"] == 1  # replaced
    await admin.post(f"{base}/registers/ccdc", files=xl(f1), data=ok_(f1))
    assert (await admin.get(f"{base}/registers?kind=tscd")).json()["total"] == 1  # kinds are separate
    # Money is not in the ordinary stocktake view.
    assert "30.000" not in (await admin.get(base)).text and "7000000" not in (await admin.get(base)).text

    async with get_sessionmaker()() as s:
        logs = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.action == "lifecycle.stocktake_register_imported", AuditLog.target_id == sid
                )
            )
        ).all()
    assert len(logs) == 3
    assert all(set(x.after) == {"filename", "sha256", "rows", "total_cost"} for x in logs)
    assert any(x.after["total_cost"] == "7000000" for x in logs)
    assert not any("TS009" in str(x.after) for x in logs)

    assert (await admin.delete(f"{base}/registers/ccdc")).status_code == 204
    assert (await admin.get(f"{base}/registers")).json()["meta"].keys() == {"tscd"}

    patched = await admin.patch(f"{base}/items/{item_id}/fa-code", json={"fa_code": " TS009 "})
    assert patched.status_code == 200 and patched.json()["fa_code"] == "TS009"
    async with get_sessionmaker()() as s:
        a = await s.scalar(
            select(AuditLog).where(
                AuditLog.action == "lifecycle.stocktake_fa_code_set", AuditLog.target_id == sid
            )
        )
    assert a is not None and a.before == {"fa_code": "TS001"} and a.after == {"fa_code": "TS009"}


async def test_closed_stocktake_refuses_register_changes(admin) -> None:  # type: ignore[no-untyped-def]
    sid, item_id = await _stocktake("closed")
    base = f"/api/lifecycle/stocktakes/{sid}"
    f = xlsx([HEAD2[0], ["TS1", "x", 1, 0, 0, None, ""]])
    r = await admin.post(
        f"{base}/registers/tscd",
        files={"file": ("a.xlsx", f, "x/y")},
        data={"confirm": "true", "preview_token": tok(f)},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "stocktake_closed"
    assert (await admin.patch(f"{base}/items/{item_id}/fa-code", json={"fa_code": "Z"})).status_code == 409
    assert (await admin.delete(f"{base}/registers/tscd")).status_code == 409


def _row(
    code: str, qty: object = 1, cost: object = "1000", net: object = "400", kind: str = "tscd", **kw: object
) -> dict:
    d = Decimal
    return {"kind": kind, "code": code, "code_norm": norm_code(code), "name": kw.pop("name", f"Sổ {code}"),
            "quantity": d(qty), "cost": d(cost) if cost is not None else None,
            "net": d(net) if net is not None else None, "department": "", "serial": "", **kw}  # type: ignore[arg-type]  # fmt: skip


def _it(code: str | None, count: str = "found", **kw: object) -> dict:
    return {"name": kw.pop("name", "LT"), "fa_code": code, "count": count, "product_type": "Laptop", **kw}


def _one(rows: list[dict], items: list[dict]) -> dict:
    out = reconcile_registers(rows, items)
    assert len(out["lines"]) == 1
    return out["lines"][0]


def test_reconcile_full_match_and_all_missing() -> None:
    d = Decimal
    ln = _one([_row("0012", cost="1000", net="400")], [_it("0012", department="Kế toán")])
    assert ln["status"] == "match" and ln["code"] == "0012" and ln["place"] == "Kế toán"
    assert ln["counted"] == ln["book"] == {"qty": d(1), "cost": d("1000.00"), "net": d("400.00")}
    assert ln["diff"] == {"qty": d(0), "cost": d(0), "net": d(0)}
    ln = _one([_row("A")], [_it("A", "missing"), _it("A", "missing")])
    assert ln["status"] == "missing_all" and ln["counted"]["qty"] == 0 and ln["counted"]["cost"] == 0
    assert ln["diff"] == {"qty": d(-1), "cost": d("-1000.00"), "net": d("-400.00")}


def test_reconcile_partial_missing_keeps_book_values_and_leaves_the_difference_to_accounting() -> None:
    ln = _one(
        [_row("A")], [_it("A", name="Case"), _it("A", "missing", asset_tag="HSC-MN-1", name="Màn hình")]
    )
    assert ln["status"] == "missing_part" and ln["counted"] == ln["book"]
    assert ln["diff"]["cost"] is None and ln["diff"]["net"] is None
    assert ln["note"] == "Thiếu thành phần: HSC-MN-1 – Kế toán xác định giá trị"


def test_reconcile_quantity_above_one_values_only_when_evenly_divisible() -> None:
    d = Decimal
    rows = [_row("B", qty=4, cost="1000", net="400")]
    ln = _one(rows, [_it("B"), _it("B"), _it("B"), _it("B", "missing")])
    assert ln["status"] == "qty_diff" and ln["counted"] == {
        "qty": d(3),
        "cost": d("750.00"),
        "net": d("300.00"),
    }
    assert ln["diff"] == {"qty": d(-1), "cost": d("-250.00"), "net": d("-100.00")} and ln["note"] == ""
    # 1000 / 3 does not split evenly: quantity only, values left to Kế toán
    ln = _one([_row("B", qty=3, cost="1000")], [_it("B"), _it("B", "missing"), _it("B", "missing")])
    assert (
        ln["counted"]["qty"] == 1
        and ln["counted"]["cost"] is None
        and ln["diff"]["cost"] is None
        and ln["note"]
    )
    # different product types: also not split
    ln = _one(rows, [_it("B"), _it("B", product_type="Monitor"), _it("B", "missing"), _it("B", "missing")])
    assert ln["counted"]["qty"] == 2 and ln["counted"]["cost"] is None and ln["note"]
    ln = _one(rows, [_it("B")] * 4)
    assert ln["status"] == "match" and ln["counted"] == ln["book"]


def test_reconcile_not_in_sdp_off_register_ccdc_and_totals() -> None:
    rows = [
        _row("T1"),
        _row("T2"),
        _row("C1", kind="ccdc"),
        _row("T1", cost="500"),
    ]  # T1 twice, each with a quantity: summed
    items = [
        _it("T1"),
        _it("c1", "found"),  # code match is normalised
        _it(None, "extra", name="Ngoài sổ", asset_tag="X1"),
        _it("ZZ", "found", name="Lạ"),
        _it(None, "missing", name="Mất"),
    ]
    out = reconcile_registers(rows, items)
    by = {(x["kind"], x["code"]): x for x in out["lines"]}
    assert by[("tscd", "T1")]["book"]["cost"] == Decimal("1500.00") and by[("tscd", "T1")]["book"]["qty"] == 2
    assert by[("tscd", "T2")]["status"] == "not_in_sdp" and by[("tscd", "T2")]["counted"]["qty"] is None
    assert by[("tscd", "T2")]["note"] == "Có trong sổ KT, không có trên SDP"
    assert by[("ccdc", "C1")]["status"] == "match"  # CCDC reconciled apart from TSCĐ
    assert [(o["name"], o["qty"]) for o in out["off_register"]] == [("Ngoài sổ", 1), ("Lạ", 1), ("Mất", 0)]
    assert "cost" not in str(out["off_register"])
    t = register_totals(out)
    assert (
        t["tscd_partial"] == 1
        and t["tscd_missing"] == 1
        and t["ccdc_matched"] == 1
        and t["off_register"] == 2  # only what was seen: "Mất" is not counted
    )


async def test_reconciliation_api_export_sheets_and_record_counts(admin, client) -> None:  # type: ignore[no-untyped-def]
    from openpyxl import load_workbook

    from app.core.documents.engine import render_docx
    from app.modules.lifecycle.stocktake_record import (
        STOCKTAKE_RECORD,
        StocktakeRecordData,
        stocktake_context,
    )
    from tests.test_documents import docx_text

    async with get_sessionmaker()() as s:
        st = Stocktake(name="KK", cutoff=date(2026, 12, 31), status="counting", created_by="t@test.local")
        s.add(st)
        await s.flush()
        s.add_all(
            [
                StocktakeItem(stocktake_id=st.id, name="LT", fa_code="000123", count="found"),
                StocktakeItem(stocktake_id=st.id, name="Lạ", count="found"),
            ]
        )
        await s.commit()
        sid = str(st.id)
    base = f"/api/lifecycle/stocktakes/{sid}"
    book = xlsx([HEAD2[0], ["000123", "Laptop", "30.000.000", 0, "12.000.000", None, ""],
                 ["000999", "Chuột", 100, 0, 50, None, ""]])  # fmt: skip
    files = {"file": ("so.xlsx", book, "application/octet-stream")}
    data = {"confirm": "true", "preview_token": tok(book)}
    assert (await admin.post(f"{base}/registers/tscd", files=files, data=data)).status_code == 200
    ccdc = xlsx([HEAD2[0], ["CC1", "Bàn", 10, 0, 0, None, ""]])  # both registers: "Lạ" is then off-register
    cdata = {"confirm": "true", "preview_token": tok(ccdc)}
    cfile = {"file": ("cc.xlsx", ccdc, "application/octet-stream")}
    assert (await admin.post(f"{base}/registers/ccdc", files=cfile, data=cdata)).status_code == 200

    got = (await admin.get(f"{base}/reconciliation")).json()
    st_by = {x["code"]: x["status"] for x in got["lines"]}
    assert st_by == {"000123": "match", "000999": "not_in_sdp", "CC1": "not_in_sdp"}
    assert got["meta"]["provisional"] is True  # the stocktake is still counting
    assert got["lines"][0]["book"]["cost"] == "30000000.00" and [o["name"] for o in got["off_register"]] == [
        "Lạ"
    ]
    assert got["totals"]["tscd_matched"] == 1 and got["totals"]["off_register"] == 1

    full = (await admin.get(f"{base}/export")).content
    wb = load_workbook(io.BytesIO(full))
    ws = wb["Mẫu 05-TSCĐ"]
    assert {"Đối chiếu CCDC", "Sổ KT chưa có trên SDP", "Chưa có trong sổ KT"} <= set(wb.sheetnames)
    assert ws["A3"].value == "Số liệu tạm – kỳ chưa kết thúc kiểm"
    assert ws["A1"].value == "BIÊN BẢN KIỂM KÊ TSCĐ" and ws["A2"].value == "Thời điểm kiểm kê 31/12/2026"
    assert {str(r) for r in ws.merged_cells.ranges} >= {"E5:G5", "H5:J5", "K5:M5", "A5:A6", "N5:N6"}
    first = [c.value for c in ws[7]]
    assert first[2] == "000123" and isinstance(first[2], str) and first[5] == Decimal("30000000.00")
    assert ws["F7"].number_format == "#,##0"
    # the per-zone file never carries the accounting sheets
    zone = (await admin.get(f"{base}/export?zone_id=none")).content
    assert "Mẫu 05-TSCĐ" not in load_workbook(io.BytesIO(zone)).sheetnames

    for role in ("operator", "viewer"):
        await _user(admin, role, f"rc-{role}@test.local")
        await client.login(f"rc-{role}@test.local")
        assert (await client.get(f"{base}/reconciliation")).status_code == 403
    await client.login("rc-viewer@test.local")  # sees the whole stocktake, but not the money sheets
    plain = await client.get(f"{base}/export")
    assert (
        plain.status_code == 200 and "Mẫu 05-TSCĐ" not in load_workbook(io.BytesIO(plain.content)).sheetnames
    )

    # record: counts only, older data without them still validates
    sample = dict(STOCKTAKE_RECORD.sample_data)
    assert StocktakeRecordData.model_validate(sample).totals.tscd_matched == 0
    sample["totals"] = {**sample["totals"], **got["totals"]}
    ctx = stocktake_context(StocktakeRecordData.model_validate(sample), "KK-1")
    text = docx_text(render_docx(STOCKTAKE_RECORD.default_template.read_bytes(), ctx))
    assert (
        "Đối chiếu sổ kế toán" in text
        and "000123" not in text
        and "30" not in text.split("Đối chiếu sổ")[1][:200]
    )


# ---------------------------------------------------------------- S-05 (K-21..K-29)

H = ["Mã TSCĐ", "Tên TSCĐ", "Số lượng", "Nguyên giá", "Hao mòn luỹ kế", "Giá trị còn lại", "Ngày ghi tăng"]


def test_k21_each_cell_is_parsed_on_its_own_and_bad_cells_are_listed() -> None:
    d = Decimal
    assert (
        parse_number("-") == 0 and parse_number("1.234-") == d(-1234) and parse_number("(1.234)") == d(-1234)
    )
    p = parse_register(
        xlsx(
            [
                H,
                [
                    "TS001",
                    "Laptop",
                    "1 bộ",
                    20000000,
                    5000000,
                    15000000,
                    None,
                ],  # bad quantity keeps the money
                ["TS002", "PC", 1, 10000000, "-", "10.000.000-", None],
                ["TS003", "Mon", 1, 1, 2, "=D4-E4", None],  # formula nobody calculated: empty cached value
            ]
        )
    )
    a, b, c = p.rows
    assert (a.cost, a.accumulated, a.net, a.quantity) == (d(20000000), d(5000000), d(15000000), None)
    assert (b.accumulated, b.net) == (0, d(-10000000))
    assert c.net is None and p.total_cost == d(30000001)
    assert p.errors == [
        {"row": 2, "column": "Số lượng", "value": "1 bộ"},
        {"row": 4, "column": "Giá trị còn lại", "value": "=D4-E4"},
    ]


def test_k22_non_data_rows_are_skipped_and_counted() -> None:
    p = parse_register(
        xlsx(
            [
                ["SỔ TÀI SẢN CỐ ĐỊNH"],
                H,
                ["A", "B", 1, 2, 3, 4, 5],  # column numbers
                ["MAYIN", "Máy in", "1", "5.000.000", "0", "5.000.000", "12/01/2025"],  # no digit in the code
                [None, "Máy móc thiết bị", None, 30000000, 0, 30000000, None],  # group heading, no code
                ["TS002", "Tổng đài", 1, 10000000, 0, 10000000, None],  # "Tổng đài" is an asset
                [None, "Tổng", None, 30000000, 0, 30000000, None],
                ["Người lập", "Kế toán trưởng"],
            ]
        )
    )
    assert [r.code for r in p.rows] == ["MAYIN", "TS002"] and p.header_rows == 1
    assert p.total_cost == Decimal(15000000)
    assert p.skipped == {"column_numbers": 1, "no_code": 1, "total": 1, "signature": 1}
    # the same helper row typed as text must not become a second header row either
    q = parse_register(xlsx([H, ["A", "B", "1", "2", "3", "4", "5"], ["TS001", "L", 1, 2, 3, 4, None]]))
    assert q.header_rows == 1 and [r.code for r in q.rows] == ["TS001"]


def test_k23_rows_with_one_code_and_no_quantity_are_one_asset() -> None:
    rows = [_row("TS1", cost="20000000", net="15000000"), _row("TS1", cost="5000000", net="5000000")]
    for r in rows:
        r["quantity"] = None
    ln = _one(rows, [_it("TS1")])
    assert ln["status"] == "match" and ln["book"]["qty"] == 1 and ln["book"]["cost"] == Decimal("25000000.00")
    assert "2 dòng cùng mã" in ln["note"]
    rows[0]["quantity"], rows[1]["quantity"] = Decimal(2), Decimal(1)  # with quantities they add up
    assert _one(rows, [_it("TS1")])["book"]["qty"] == 3


def test_k24_off_register_counts_only_seen_items_and_needs_both_registers() -> None:
    items = [_it("ZZ", "found", name="Lạ"), _it(None, "missing", name="Mất")]
    empty = reconcile_registers([], items)
    assert (
        empty["lines"] == [] and empty["off_register"] == [] and set(register_totals(empty).values()) == {0}
    )
    one = reconcile_registers([_row("T1")], items)  # only TSCĐ: the CCDC register may hold them
    assert one["off_register"] == [] and one["off_register_complete"] is False
    both = reconcile_registers([_row("T1"), _row("C1", kind="ccdc")], items)
    assert both["off_register_complete"] is True and register_totals(both)["off_register"] == 1


def test_k25_pending_is_neither_found_nor_lost() -> None:
    ln = _one([_row("A")], [_it("A", "pending")])
    assert ln["status"] == "pending" and ln["counted"]["qty"] is None and ln["diff"]["cost"] is None
    assert ln["note"] == "Chưa kiểm" and ln["book"]["cost"] == Decimal("1000.00")
    assert _one([_row("A")], [_it("A", "found"), _it("A", "pending")])["status"] == "pending"


def test_k26_sparse_wide_sheet_stays_bounded_and_zip_bombs_are_refused() -> None:
    import tracemalloc
    import zipfile

    wb = Workbook()
    ws = wb.active
    ws.append(["Mã TSCĐ", "Tên TSCĐ", "Nguyên giá"])
    for i in range(300):
        ws.append([f"TS{i}", "x", 1])
        ws.cell(ws.max_row, 16384, 1)  # a stray cell in the last column
    buf = io.BytesIO()
    wb.save(buf)
    tracemalloc.start()
    p = parse_register(buf.getvalue())
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(p.rows) == 300 and peak < 60 * 2**20
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/big.bin", b"\0" * (60 * 2**20))
    assert len(bomb.getvalue()) < 5 * 2**20
    with pytest.raises(AppError) as e:
        parse_register(bomb.getvalue())
    assert e.value.code == "file_too_large"


def test_k27_a_matched_item_leaves_ccdc_and_serials_must_be_long_and_unique() -> None:
    out = reconcile_registers([_row("S1"), _row("S1", kind="ccdc")], [_it("S1")])
    by = {x["kind"]: x["status"] for x in out["lines"]}
    assert by == {"tscd": "match", "ccdc": "not_in_sdp"} and "S1" in out["warnings"][0]
    r = {"code_norm": "TS9", "serial": "N/A"}
    assert match_register([r], [{"id": 1, "fa_code": "", "serial": "N/A"}], "tscd")[0]["items"] == []
    r = {"code_norm": "TS9", "serial": "SN-12345"}
    two = [{"id": 1, "fa_code": "", "serial": "sn-12345"}, {"id": 2, "fa_code": "", "serial": "SN-12345"}]
    assert match_register([r], two, "tscd")[0]["items"] == []  # ambiguous
    assert [i["id"] for i in match_register([r], two[:1], "tscd")[0]["items"]] == [1]


def test_k29_columns_can_be_chosen_by_hand_and_new_aliases_match() -> None:
    rows = [["Mã nội bộ", "Tên", "Giá gốc"], ["X1", "Laptop", "5.000"]]
    with pytest.raises(AppError) as e:
        parse_register(xlsx(rows))
    assert e.value.code == "register_header_not_found" and "Mã nội bộ" in e.value.details["headers"]
    p = parse_register(xlsx(rows), mapping={"code": "A", "name": "B", "cost": "C"})
    assert p.rows[0].code == "X1" and p.rows[0].cost == 5000 and p.columns["cost"] == "Giá gốc"
    assert p.headers[2] == {"col": "C", "top": "", "sub": "Giá gốc"}
    with pytest.raises(AppError) as e:
        parse_register(xlsx(rows), mapping={"code": "ZZ", "name": "B"})
    assert e.value.code == "register_header_not_found"
    with pytest.raises(AppError) as e:
        parse_register(xlsx([H, ["T", "n", 1, 2, 3, 4, None]]), mapping={"code": "A", "bogus": "B"})
    assert e.value.code == "register_mapping_invalid"
    q = parse_register(xlsx([["Số hiệu TSCĐ", "Tên TSCĐ", "Nguyên giá"], ["T1", "n", 1]]))
    assert q.rows[0].code == "T1"


async def test_k21_k28_import_is_bound_to_the_preview_and_blocked_by_bad_cells(admin) -> None:  # type: ignore[no-untyped-def]
    from app.core.storage.models import StoredFile

    sid, _ = await _stocktake()
    base = f"/api/lifecycle/stocktakes/{sid}"
    good = xlsx([H, ["TS1", "L", 1, 100, 0, 100, None], [None, "Tổng", None, 100, 0, 100, None]])
    bad = xlsx([H, ["TS1", "L", "1 bộ", 100, 0, 100, None]])
    f = lambda b: {"file": ("so.xlsx", b, "application/octet-stream")}  # noqa: E731
    async with get_sessionmaker()() as s:
        files_before = len((await s.scalars(select(StoredFile))).all())

    prev = (await admin.post(f"{base}/registers/tscd/preview", files=f(bad))).json()
    assert prev["errors"] == [{"row": 2, "column": "Số lượng", "value": "1 bộ"}] and prev["errors_total"] == 1
    assert (
        prev["mapping"]["code"] == "A"
        and {"col": "D", "top": "", "sub": "Nguyên giá"} in prev["headers"]
        and prev["skipped"] == {}
    )
    r = await admin.post(
        f"{base}/registers/tscd", files=f(bad), data={"confirm": "true", "preview_token": tok(bad)}
    )
    assert r.json()["error"]["code"] == "register_bad_cells"
    assert (await admin.get(f"{base}/registers")).json()["total"] == 0

    assert (await admin.post(f"{base}/registers/tscd/preview", files=f(good))).json()["skipped"] == {
        "total": 1
    }
    r = await admin.post(  # a different file than the one previewed
        f"{base}/registers/tscd", files=f(good), data={"confirm": "true", "preview_token": tok(bad)}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "register_file_changed"
    ok = await admin.post(
        f"{base}/registers/tscd", files=f(good), data={"confirm": "true", "preview_token": tok(good)}
    )
    assert (
        ok.status_code == 200
        and ok.json()["sha256"] == hashlib.sha256(good).hexdigest()
        and "file_id" not in ok.json()
    )
    assert ok.json()["filename"] == "so.xlsx"
    async with get_sessionmaker()() as s:  # the workbook itself is not kept
        assert len((await s.scalars(select(StoredFile))).all()) == files_before
    # mapping override travels through preview and import
    odd = xlsx([["Mã nội bộ", "Tên", "Giá gốc"], ["X1", "Laptop", 7]])
    m = '{"code": "A", "name": "B", "cost": "C"}'
    assert (await admin.post(f"{base}/registers/tscd/preview", files=f(odd))).status_code == 400
    assert (
        await admin.post(f"{base}/registers/tscd/preview", files=f(odd), data={"mapping": m})
    ).status_code == 200
    ok = await admin.post(
        f"{base}/registers/tscd",
        files=f(odd),
        data={"confirm": "true", "preview_token": tok(odd, m), "mapping": m},
    )
    assert ok.status_code == 200 and ok.json()["total_cost"] == "7"


def test_k26_import_parses_before_it_takes_the_stocktake_lock() -> None:
    import inspect

    from app.modules.lifecycle import stocktake_api

    src = inspect.getsource(stocktake_api.import_register)
    assert src.index("_parse_upload(") < src.index("lock=True")


async def test_k29_money_needs_finance_and_view_all_and_export_audit_says_so(admin, client) -> None:  # type: ignore[no-untyped-def]
    sid, _ = await _stocktake()
    base = f"/api/lifecycle/stocktakes/{sid}"
    book = xlsx([H, ["TS1", "L", 1, 100, 0, 100, None]])
    data = {"confirm": "true", "preview_token": tok(book)}
    assert (
        await admin.post(f"{base}/registers/tscd", files={"file": ("a.xlsx", book, "x/y")}, data=data)
    ).status_code == 200
    await admin.post(
        "/api/core/roles",
        json={"code": "fin_only", "name": "Fin only", "permissions": ["lifecycle.stocktake.finance"]},
    )
    await _user(admin, "fin_only", "fin-only@test.local")
    await client.login("fin-only@test.local")
    for path in ("reconciliation", "registers"):
        assert (await client.get(f"{base}/{path}")).status_code == 403
    assert (
        await client.post(f"{base}/registers/tscd/preview", files={"file": ("a.xlsx", book, "x/y")})
    ).status_code == 403
    assert (await admin.get(f"{base}/export")).status_code == 200
    assert (await admin.get(f"{base}/export?zone_id=none")).status_code == 200
    async with get_sessionmaker()() as s:
        logs = (
            await s.scalars(
                select(AuditLog)
                .where(AuditLog.action == "lifecycle.stocktake_exported", AuditLog.target_id == sid)
                .order_by(AuditLog.id)
            )
        ).all()
    assert [x.after for x in logs] == [{"finance_sheets": True}, {"finance_sheets": False}]


def _two_tier() -> bytes:
    """Opening/closing balance groups (merged on top), each with Nguyên giá / Hao mòn luỹ kế / Giá trị còn lại."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sổ"
    ws.append(["Mã TSCĐ", "Tên TSCĐ", "Số dư đầu kỳ", None, None, "Số dư cuối kỳ", None, None])
    ws.append([None, None, *["Nguyên giá", "Hao mòn luỹ kế", "Giá trị còn lại"] * 2])
    ws.append(["TS1", "Laptop", 100, 10, 90, 150, 40, 110])
    ws.merge_cells("C1:E1")
    ws.merge_cells("F1:H1")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_k31_same_label_in_two_groups_is_ambiguous_until_columns_are_chosen() -> None:
    p = parse_register(_two_tier())
    assert set(p.ambiguous) == {"cost", "accumulated", "net"}
    assert [c["col"] for c in p.ambiguous["cost"]] == ["C", "F"]
    assert (
        p.ambiguous["accumulated"][1]["label"] == "Số dư cuối kỳ – Hao mòn luỹ kế"
    )  # merged label runs right
    assert "cost" not in p.positions and p.positions["code"] == "A"
    q = parse_register(_two_tier(), mapping={"cost": "F", "accumulated": "G", "net": "H"})
    assert (
        q.ambiguous == {} and q.rows[0].cost == 150 and q.rows[0].net == 110 and q.rows[0].accumulated == 40
    )
    assert q.positions["cost"] == "F" and q.columns["cost"] == "Số dư cuối kỳ – Nguyên giá"
    with pytest.raises(AppError):  # a letter outside the header row
        parse_register(_two_tier(), mapping={"cost": "ZZ"})


def test_k31_nan_and_infinity_are_cell_errors() -> None:
    for bad in ("NaN", "Infinity", "-inf", float("nan"), float("inf")):
        with pytest.raises(ValueError):
            parse_number(bad)
    p = parse_register(xlsx([H, ["TS1", "L", "NaN", 1, 0, 1, None]]))
    assert [e["value"] for e in p.errors] == ["NaN"]


async def test_k31_k32_import_needs_the_token_of_this_sheet_and_mapping_and_no_ambiguity(admin) -> None:  # type: ignore[no-untyped-def]
    sid, _ = await _stocktake()
    url = f"/api/lifecycle/stocktakes/{sid}/registers/tscd"
    book = _two_tier()
    f = {"file": ("so.xlsx", book, "application/octet-stream")}
    m = '{"cost": "F", "accumulated": "G", "net": "H"}'
    prev = (await admin.post(f"{url}/preview", files=f)).json()
    assert set(prev["ambiguous"]) == {"cost", "accumulated", "net"} and prev["preview_token"] == tok(book)
    r = await admin.post(url, files=f, data={"confirm": "true", "preview_token": prev["preview_token"]})
    assert r.json()["error"]["code"] == "register_ambiguous_columns"
    # a token minted without the mapping does not authorise the mapped import
    r = await admin.post(url, files=f, data={"confirm": "true", "preview_token": tok(book), "mapping": m})
    assert r.status_code == 409 and r.json()["error"]["code"] == "register_file_changed"
    r = await admin.post(
        url, files=f, data={"confirm": "true", "preview_token": tok(book, m, "Khác"), "mapping": m}
    )
    assert r.status_code == 409  # token of another sheet
    pm = (await admin.post(f"{url}/preview", files=f, data={"mapping": m})).json()
    assert pm["ambiguous"] == {} and pm["total_cost"] == "150" and pm["mapping"]["cost"] == "F"
    ok = await admin.post(
        url, files=f, data={"confirm": "true", "preview_token": pm["preview_token"], "mapping": m}
    )
    assert ok.status_code == 200 and ok.json()["total_cost"] == "150"
