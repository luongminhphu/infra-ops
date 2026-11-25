from datetime import date

import pytest

from app.modules.lifecycle import normalize
from app.modules.lifecycle.parsing import (
    classify_change_type,
    fold,
    html_to_text,
    parse_rtr_description,
    split_person,
)
from app.modules.lifecycle.reconcile import reconcile
from app.modules.lifecycle.settings import LifecycleSettings
from tests.lifecycle_fixtures import ASSETS, BIOSTAR_USERS, SDP_USERS, rtr_html

S = LifecycleSettings()


def test_parse_rtr_description_extracts_fields() -> None:
    p = parse_rtr_description(
        rtr_html(
            "003551", "Vũ Thị Thảo Vân", "Chính thức", "27/11/2026", manager="Tôn Thất Phi Dũng (003501)"
        ),
        S,
    )
    assert p.employee_id == "003551"  # leading zeros kept
    assert p.full_name == "Vũ Thị Thảo Vân"
    assert p.effective_date == date(2026, 11, 27)
    assert p.fields["department"] == "Kinh Doanh"
    assert p.fields["work_location"] == "Chi nhánh 1"
    assert p.manager_name == "Tôn Thất Phi Dũng"
    assert p.manager_employee_id == "003501"
    # "Chính thức" alone is ambiguous → must be mapped by an admin
    assert p.kind is None
    assert any("Chính thức" in e for e in p.errors)


def test_parse_reports_missing_and_invalid_values() -> None:
    p = parse_rtr_description("<p>Loại biến động: Nhân viên mới</p><p>3. Ngày hiệu lực: 2026/31/12</p>", S)
    assert p.employee_id is None
    assert any("Mã nhân viên" in e for e in p.errors)
    assert any("dd/MM/yyyy" in e for e in p.errors)
    assert p.kind == "onboard"


def test_custom_labels_and_mapping() -> None:
    s = LifecycleSettings(
        field_labels={"employee_id": "Employee code"}, change_type_map={"Chính thức": "onboard"}
    )
    p = parse_rtr_description("Loại biến động: Chính thức<br>Employee code: 000777<br>Họ &amp; tên: A", s)
    assert p.employee_id == "000777"
    assert p.kind == "onboard"
    assert p.errors == []


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("Nhân viên mới", "onboard"),
        ("Chuyển từ thử việc sang chính thức", "probation_to_official"),
        ("Thuyên chuyển", "transfer"),
        ("Nghỉ việc", "offboard"),
        ("Khác", None),
    ],
)
def test_classify_change_type_keywords(raw: str, kind: str | None) -> None:
    assert classify_change_type(raw, S) == kind


def test_helpers() -> None:
    assert fold("Đỗ  Minh   Châu") == "do minh chau"
    assert html_to_text("A<br/>B&nbsp;&amp;C") == "A\nB &C"
    assert split_person("Nguyễn A") == ("Nguyễn A", None)


def test_normalize_biostar_states() -> None:
    active = normalize.biostar_user(BIOSTAR_USERS[0])
    assert active["active"] and active["cards"] == 1 and active["fingerprints"] == 2
    assert active["access_groups"] == ["Văn phòng HCM"]
    assert normalize.biostar_user(BIOSTAR_USERS[2])["active"] is False
    expired = normalize.biostar_user({"user_id": "1", "expiry_datetime": "2020-01-01T00:00:00.00Z"})
    assert expired["expired"] and not expired["active"]


def test_normalize_sdp_nested_employee_field() -> None:
    u = normalize.sdp_user(
        {"id": 5, "name": "X", "udf_fields": {"udf_char1": "000123"}}, "udf_fields.udf_char1"
    )
    assert u["employee_id"] == "000123"


def _data():  # type: ignore[no-untyped-def]
    requests = [
        {
            "id": "1001",
            "employee_id": "003551",
            "full_name": "Nguyễn Văn An",
            "kind": "onboard",
            "effective_date": "2026-11-02",
            "created_at": "2026-11-01",
            "parse_errors": [],
        },
        {
            "id": "1002",
            "employee_id": "002100",
            "full_name": "Phạm Thị Bình",
            "kind": "offboard",
            "effective_date": "2026-11-05",
            "created_at": "2026-11-03",
            "parse_errors": [],
        },
        {
            "id": "1003",
            "employee_id": None,
            "subject": "x",
            "kind": None,
            "parse_errors": ["Thiếu Mã nhân viên"],
        },
    ]
    users = [normalize.sdp_user(u, "employee_id") for u in SDP_USERS]
    assets = [normalize.sdp_asset(a) for a in ASSETS]
    bs = [normalize.biostar_user(b) for b in BIOSTAR_USERS]
    return requests, users, assets, bs


def _issues(result, emp):  # type: ignore[no-untyped-def]
    row = next(r for r in result.rows if r.employee_id == emp)
    return {i.rule for i in row.issues}, row


def test_reconcile_rules() -> None:
    requests, users, assets, bs = _data()
    result = reconcile(requests, users, assets, bs, S, date(2026, 11, 16))
    # Offboarded but BioStar active and laptop still assigned
    rules, row = _issues(result, "002100")
    assert {"offboard_biostar_active", "offboard_assets_in_use"} <= rules
    assert row.cells["assets"]["label"] == "1"
    # name differs only by diacritics → not a mismatch
    assert "name_mismatch" not in rules
    # Onboarded 14 days ago: no BioStar user, no assets
    rules, _ = _issues(result, "003551")
    assert {"onboard_no_biostar", "onboard_no_assets"} <= rules
    # BioStar user with unknown employee ID
    rules, _ = _issues(result, "009999")
    assert rules == {"biostar_unknown_employee"}
    # Request without employee ID becomes a global note; printer state notes for unassigned "In Use"
    assert any(n["rule"] == "request_parse_error" for n in result.notes)
    # Disabled BioStar, no request → no issue
    rules, _ = _issues(result, "001500")
    assert rules == set()


def test_reconcile_respects_grace_and_failed_sources() -> None:
    requests, users, assets, bs = _data()
    # Day of onboarding: within 3-day grace → no onboarding issues yet
    result = reconcile(requests, users, assets, bs, S, date(2026, 11, 3))
    rules, _ = _issues(result, "003551")
    assert not rules & {"onboard_no_biostar", "onboard_no_assets"}
    # BioStar unreadable → BioStar rules skipped, cell says so
    result = reconcile(
        requests, users, assets, [], S, date(2026, 11, 16), {"sdp": True, "assets": True, "biostar": False}
    )
    rules, row = _issues(result, "002100")
    assert "offboard_biostar_active" not in rules and "offboard_assets_in_use" in rules
    assert row.cells["biostar"]["status"] == "skipped"


def test_reconcile_duplicate_and_name_fallback() -> None:
    users = [
        normalize.sdp_user(u, "employee_id")
        for u in [
            {"id": "x1", "name": "A", "employee_id": "1"},
            {"id": "x2", "name": "B", "employee_id": "1"},
        ]
    ]
    result = reconcile([], users, [], [], S, date(2026, 11, 16))
    assert {i.rule for i in result.rows[0].issues} >= {"sdp_duplicate_employee_id"}
    # No employee_id on SDP users → global note, assets matched by unique name
    users = [normalize.sdp_user({"id": "u9", "name": "Lê Văn D"}, "employee_id")]
    assets = [
        normalize.sdp_asset({"id": "z", "name": "LT", "state": {"name": "In Use"}, "user": {"id": "u9"}})
    ]
    req = [
        {
            "id": "r",
            "employee_id": "000555",
            "full_name": "Le Van D",
            "kind": "onboard",
            "effective_date": "2026-11-01",
            "parse_errors": [],
        }
    ]
    result = reconcile(req, users, assets, [], S, date(2026, 11, 16))
    assert any(n["rule"] == "sdp_no_employee_ids" for n in result.notes)
    row = result.rows[0]
    assert row.asset_ids == ["z"] and "nối theo họ tên" in row.cells["assets"]["detail"]


def test_allocation_reuses_stock_by_date_and_band() -> None:
    from datetime import date

    from app.modules.lifecycle.allocation import Need, allocate, items_for
    from app.modules.lifecycle.settings import StandardItem

    std = [
        StandardItem(label="Laptop", product_types=["Laptop"]),
        StandardItem(
            label="Laptop cao cấp", bands=["B5", "b6"], product_types=["Laptop"], product_contains="XPS"
        ),
        StandardItem(label="Màn hình", product_types=["Monitor"], quantity=2),
    ]
    assert [i.label for i in items_for("b 6", std)] == ["Laptop", "Laptop cao cấp", "Màn hình"]
    stock = [
        {"id": "l1", "product_type": "Laptop", "product": "Dell Latitude"},
        {"id": "l2", "product_type": "laptop", "product": "Dell XPS 13"},
        {"id": "m1", "product_type": "Monitor", "product": "P2422H"},
        {"id": "m2", "product_type": "Monitor", "product": "P2422H"},
    ]
    needs = [
        Need("late", date(2026, 12, 20), "B3"),
        Need("early", date(2026, 12, 1), "B5", picked=[{"id": "m2", "product_type": "Monitor"}]),
        Need("noband", date(2026, 11, 30), None),
    ]
    plan = allocate(needs, std, stock)
    early = {ln.label: ln for ln in plan["early"]}
    # The earlier onboard is served first; its draft-picked monitor counts before free stock.
    assert [a["id"] for a in early["Laptop"].reuse] == ["l1"]
    assert [a["id"] for a in early["Laptop cao cấp"].reuse] == ["l2"]
    assert [a["id"] for a in early["Màn hình"].picked] == ["m2"] and [
        a["id"] for a in early["Màn hình"].reuse
    ] == ["m1"]
    late = {ln.label: ln for ln in plan["late"]}
    assert late["Laptop"].buy == 1 and late["Màn hình"].buy == 2 and "Laptop cao cấp" not in late
    assert plan["noband"] == []


def test_biostar_employee_id_last_six_digits_and_merge() -> None:
    from app.modules.lifecycle.normalize import id_key, merge_biostar_users, resolve_employee_id

    assert id_key("1002352") == "002352" and id_key("002352") == "002352" and id_key("2352") == "2352"
    assert id_key("GUEST-01") == "GUEST-01"  # non-numeric IDs are compared as they are
    sdp = {"002352": "002352", "000008": "000008"}  # last 6 digits → SDP employee ID
    assert resolve_employee_id("1002352", sdp) == "002352"
    assert resolve_employee_id("000008", sdp) == "000008"
    assert resolve_employee_id("100008", sdp) == "100008"  # last 6 digits differ from SDP's: stays (flagged)
    old = {"user_id": "100002", "employee_id": "000002", "active": True, "source": "Office"}
    new = {"user_id": "000002", "employee_id": "000002", "active": False, "source": "Server"}
    assert merge_biostar_users([new, old]) == [old]  # an active user anywhere wins (offboard checks)
    both_off = {**old, "active": False}
    assert merge_biostar_users([new, both_off]) == [new]  # otherwise the main system (read first)


def test_retired_biostar_group_is_not_reconciled() -> None:
    requests, users, assets, bs = _data()
    # 002100 offboarded and moved to the "left" group (still enabled): no offboard_biostar_active issue.
    moved = [
        {**b, "retired": True, "active": False} if b["user_id"] in ("002100", "009999") else b for b in bs
    ]
    result = reconcile(requests, users, assets, moved, S, date(2026, 11, 16))
    rules, row = _issues(result, "002100")
    assert "offboard_biostar_active" not in rules
    assert row.cells["biostar"]["label"] == "Nghỉ việc"  # kept: an RTR request concerns 002100
    # Without an RTR request a person in that group is hidden, even with an SDP user.
    assert "009999" not in {r.employee_id for r in result.rows}
    # 001500: SDP user + BioStar user, no RTR request.
    assert "001500" in {r.employee_id for r in result.rows}
    left = [{**b, "retired": True, "active": False} if b["user_id"] == "001500" else b for b in bs]
    assert "001500" not in {
        r.employee_id for r in reconcile(requests, users, assets, left, S, date(2026, 11, 16)).rows
    }


def test_name_key_ignores_order_and_punctuation() -> None:
    from app.modules.lifecycle.reconcile import name_key

    assert name_key("Thinh. Nguyen Canh") == name_key("NGUYEN CANH THINH") == name_key("Nguyễn Cảnh Thịnh")
    assert name_key("Nguyen Canh Thinh") != name_key("Nguyen Van Thinh")
