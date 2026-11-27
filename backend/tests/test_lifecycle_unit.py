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
    # RTR "Chính thức" = probation ends (an admin mapping can still override it)
    assert p.kind == "probation_to_official"
    assert p.errors == []


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
        # The five RTR change types sent to SDP
        ("Thực tập sinh/CTV", "onboard"),
        ("Tân tuyển - TV", "onboard"),
        ("Chính thức", "probation_to_official"),
        ("Thay đổi cấp trên trực tiếp", "transfer"),
        ("Thôi việc", "offboard"),
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


def test_asset_serial_is_the_org_serial_number() -> None:
    base = {"id": "1", "name": "LT", "serial_number": "SN-OLD", "computer_system": {"service_tag": "ST"}}
    assert normalize.sdp_asset({**base, "org_serial_number": "ORG-1"})["serial"] == "ORG-1"
    assert normalize.sdp_asset(base)["serial"] == "SN-OLD"  # assets without it keep the serial number
    assert normalize.sdp_asset({"id": "2", "computer_system": {"service_tag": "ST"}})["serial"] == "ST"
    a = normalize.sdp_asset(
        {**base, "site": {"name": "HCM"}, "udf_fields": {"udf_char1": "TSCD-01", "x": None}}
    )
    assert (a["site"], a["udf"]) == ("HCM", {"udf_char1": "TSCD-01"})


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
    assert row.cells["assets"]["label"] == "1 tài sản"
    # name differs only by diacritics → not a mismatch
    assert "name_mismatch" not in rules
    # Onboarded 14 days ago: no BioStar user, no assets
    rules, _ = _issues(result, "003551")
    assert {"onboard_no_biostar", "onboard_no_assets"} <= rules
    # BioStar user with an employee ID unknown to SDP: not an employee row, listed apart for access review
    assert all(r.employee_id != "009999" for r in result.rows)
    unknown = {n["employee_id"]: n for n in result.notes if n["rule"] == "biostar_unknown_employee"}["009999"]
    assert unknown["employee_id"] == "009999" and unknown["active"] is True
    assert "còn quyền ra vào" in unknown["message"]
    # SDP unreadable: nothing can be judged, so BioStar users still show as rows
    unread = reconcile(
        requests, users, assets, bs, S, date(2026, 11, 16), {"sdp": False, "assets": True, "biostar": True}
    )
    assert any(r.employee_id == "009999" for r in unread.rows)
    assert not any(n["rule"] == "biostar_unknown_employee" for n in unread.notes)
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


def test_sdp_users_are_joined_by_employee_id_only() -> None:
    """An SDP user without an employee ID (synced from AD) is never taken as an RTR employee's by name."""
    users = [
        normalize.sdp_user({"id": "h1", "name": "Hau. Nguyen Thi My"}, "employee_id"),
        normalize.sdp_user(
            {"id": "h2", "name": "Chau. Phan Trong Bao", "employee_id": "<003456>"}, "employee_id"
        ),
    ]
    assert users[1]["employee_id"] == "003456"  # brackets typed in SDP are not part of the ID
    req = [{"id": "7091", "employee_id": "003475", "full_name": "Nguyễn Thị Mỹ Hậu", "kind": "transfer",
            "effective_date": "2026-10-12", "parse_errors": []}]  # fmt: skip
    row = next(
        r for r in reconcile(req, users, [], [], S, date(2026, 11, 26)).rows if r.employee_id == "003475"
    )
    assert row.sdp_user_ids == [] and row.cells["sdp"]["label"] == "RTR"


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


def test_biostar_employee_id_last_five_digits_and_merge() -> None:
    from app.modules.lifecycle.normalize import id_key, merge_biostar_users, resolve_employee_id, sdp_id_index

    assert id_key("100008") == "00008" and id_key("1002352") == "02352" and id_key("2352") == "2352"
    assert id_key("GUEST-01") == "GUEST-01"  # non-numeric IDs are compared as they are
    sdp = sdp_id_index(
        [
            {"employee_id": e, "name": n}
            for e, n in [
                ("002352", "A"),
                ("000008", "Thinh. Nguyen Canh"),
                ("100100", "Binh. Tran"),
                ("000100", "An. Le"),
                ("003273", "Minh. Lam Ngoc Gia"),
                ("103273", "Minh. Lam Ngoc Gia"),  # stale second SDP account with the same name
                ("203273", "Hoa. Pham"),
                ("003273", "Minh. Lam Ngoc Gia"),  # duplicate record, same ID: still one candidate
            ]
        ]
    )
    r = resolve_employee_id
    assert r("1002352", "", sdp) == "002352"
    assert r("100008", "", sdp) == "000008"  # Office 100008 = SDP 000008
    assert r("000008", "", sdp) == "000008"
    assert r("200100", "", sdp) == "200100"  # 00100 is shared by two SDP IDs, no name: not guessed
    assert r("200100", "AN LE", sdp) == "000100"  # shared digits: the name decides
    assert r("103273", "LAM NGOC GIA MINH", sdp) == "103273"  # identical SDP ID wins
    assert r("303273", "HOA PHAM", sdp) == "203273"
    assert r("303273", "LAM NGOC GIA MINH", sdp) == "303273"  # two SDP IDs with that name: not guessed
    assert r("777777", "", sdp) == "777777"  # unknown to SDP: kept as is (flagged)
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
    assert row.cells["biostar"]["label"] == "Nhóm loại trừ"  # kept: an RTR request concerns 002100
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


def test_latest_request_is_the_one_a_job_is_for() -> None:
    # 002100 was onboarded months ago (closed request 0900) and is now leaving (1002): a Job is only for 1002.
    requests, users, assets, bs = _data()
    old = {
        **requests[1],
        "id": "0900",
        "kind": "onboard",
        "effective_date": "2026-05-04",
        "created_at": "2026-05-01",
    }
    result = reconcile([*requests, old], users, assets, bs, S, date(2026, 11, 16))
    row = next(r for r in result.rows if r.employee_id == "002100")
    assert set(row.request_ids) == {"1002", "0900"} and row.latest_request_id == "1002"
    assert row.to_dict()["latest_request_id"] == "1002"


def test_returned_stock_is_suggested_first() -> None:
    from app.modules.lifecycle.allocation import Need, allocate, returned_first
    from app.modules.lifecycle.settings import StandardItem

    stock = [
        {"id": "new1", "product_type": "Laptop", "name": "LT-NEW"},
        {"id": "old1", "product_type": "Laptop", "name": "LT-OLD"},
    ]
    ordered = returned_first(stock, {"old1"})
    assert (
        [a["id"] for a in ordered] == ["old1", "new1"]
        and ordered[0]["returned"]
        and not ordered[1]["returned"]
    )
    plan = allocate(
        [Need("r1", date(2026, 12, 1), "B3", [])],
        [StandardItem(label="Laptop", product_types=["Laptop"])],
        ordered,
    )
    assert [a["id"] for a in plan["r1"][0].reuse] == [
        "old1"
    ]  # the returned laptop goes out, the new one stays


def test_synced_asset_types_include_the_allocation_standard() -> None:
    from app.modules.lifecycle.settings import LifecycleSettings, StandardItem
    from app.modules.lifecycle.sync import asset_types

    s = LifecycleSettings(
        asset_product_types=["Laptop"],
        allocation_standard=[StandardItem(label="UPS", product_types=["UPS", " IP Phone "], quantity=1)],
    )
    # Non-IT types (UPS, IP Phone) named only in the standard are still read, so stock shows them.
    assert asset_types(s) == {"laptop", "ups", "ip phone"}
    assert asset_types(LifecycleSettings(asset_product_types=[])) == set()  # empty = every type


def test_old_retired_groups_setting_joins_the_excluded_groups() -> None:
    from app.modules.lifecycle.settings import LifecycleSettings

    s = LifecycleSettings.model_validate(
        {"biostar_retired_groups": ["0.NGHIVIEC"], "biostar_excluded_groups": ["3. VENDOR", "0.NGHIVIEC"]}
    )
    assert s.biostar_excluded_groups == ["0.NGHIVIEC", "3. VENDOR"]
    assert "biostar_retired_groups" not in s.model_dump()


def test_allocation_matrix_rows_by_job_title() -> None:
    from app.modules.lifecycle.allocation import items_for
    from app.modules.lifecycle.settings import (
        AllocationMatrix,
        LifecycleSettings,
        MatrixCell,
        MatrixDevice,
        MatrixLevel,
        StandardItem,
        matrix_from_standard,
    )

    on = lambda q=1, tier="": MatrixCell(on=True, quantity=q, tier=tier)  # noqa: E731
    off = MatrixCell()
    devices = [MatrixDevice(label="PC", product_types=["Desktop"]), MatrixDevice(label="Laptop", product_types=["Laptop"]),
               MatrixDevice(label="Màn hình", product_types=["Monitor"])]  # fmt: skip
    levels = [
        MatrixLevel(name="Intern / Vendor", titles=["Thực tập sinh", "Intern", "Vendor"]),
        MatrixLevel(name="Nhân viên"),  # the plain row: every other title
        MatrixLevel(name="Nhân viên kỹ thuật", departments=["IT", "DATA"]),
        MatrixLevel(name="Quản lý", titles=["Manager", "Director", "Trưởng phòng"]),
        MatrixLevel(name="Lãnh đạo cấp cao", titles=["Senior Director", "C-level", "Giám đốc điều hành"]),
    ]
    cells = [
        [on(tier="PC - Standard"), off, on(1)],
        [on(tier="PC - Standard"), off, on(1)],
        [on(tier="PC - Performance"), off, on(2)],
        [on(tier="PC - Performance"), on(tier="Laptop - Standard"), on(2)],
        [on(tier="PC - Performance"), on(tier="Laptop - Performance"), on(2)],
    ]
    s = LifecycleSettings(allocation_matrix=AllocationMatrix(devices=devices, levels=levels, cells=cells))

    def row(title: str | None, dept: str | None = None, chosen: str | None = None) -> str | None:
        return s.items_for_employee(title, None, dept, chosen)[1]

    assert row("Chuyên viên phân tích") == "Nhân viên"
    assert row("Chuyên viên phân tích", "IT") == "Nhân viên kỹ thuật"  # department row beats the plain row
    assert row("Thực tập sinh IT", "IT") == "Intern / Vendor"  # a title keyword beats a department-only row
    assert row("Senior Manager - Risk", "IT") == "Quản lý"
    assert row("Senior Director, Khối Đầu tư") == "Lãnh đạo cấp cao"  # longest keyword wins over "Director"
    assert row("giam doc dieu hanh") == "Lãnh đạo cấp cao"  # accents and case ignored
    assert row("Managerial assistant") == "Nhân viên"  # whole words only
    assert row("Chuyên viên", chosen="Quản lý") == "Quản lý"  # IT picked the row by hand
    items, level, why = s.items_for_employee("Senior Manager", None, "Kế toán")
    assert level == "Quản lý" and why == "chức danh có “Manager”"
    assert sorted((i.label, i.quantity) for i in items) == [
        ("Laptop - Standard", 1),
        ("Màn hình", 2),
        ("PC - Performance", 1),
    ]

    # Cells must name a tier from the reference table; one plain row at most; row names are unique.
    bad = [[MatrixCell(on=True, tier="PC - Ultra")]]
    with pytest.raises(ValueError, match="PC - Ultra"):
        LifecycleSettings(
            allocation_matrix=AllocationMatrix(devices=devices[:1], levels=levels[:1], cells=bad)
        )
    with pytest.raises(ValueError, match="Chỉ một hàng"):
        AllocationMatrix(devices=[], levels=[MatrixLevel(name="a"), MatrixLevel(name="b")], cells=[[], []])
    with pytest.raises(ValueError, match="khác nhau"):
        AllocationMatrix(
            devices=[], levels=[MatrixLevel(name="A", titles=["x"]), MatrixLevel(name="a")], cells=[[], []]
        )

    # Without a row that fits (no plain row) the employee has no standard yet.
    strict = AllocationMatrix(devices=devices[:1], levels=levels[3:4], cells=[[on()]])
    assert LifecycleSettings(allocation_matrix=strict).items_for_employee("Chuyên viên", None)[0] is None

    # Hand-written band lines become a first matrix that allocates the same.
    legacy = [StandardItem(label="Laptop", bands=["B5", "B6"], product_types=["Laptop"]),
              StandardItem(label="Màn hình", product_types=["Monitor"], quantity=2)]  # fmt: skip
    converted = LifecycleSettings(allocation_matrix=matrix_from_standard(legacy))
    for band in ("B5", "b 6", "B1"):
        got = converted.items_for_employee(None, band)[0] or []
        assert sorted((i.label, i.quantity) for i in got) == sorted(
            (i.label, i.quantity) for i in items_for(band, legacy)
        ), band
