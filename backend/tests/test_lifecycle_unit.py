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


def test_reuse_only_line_takes_returned_devices_and_waits() -> None:
    from datetime import date

    from app.modules.lifecycle.allocation import Need, allocate, returned_first
    from app.modules.lifecycle.settings import (
        AllocationMatrix,
        LifecycleSettings,
        MatrixCell,
        MatrixDevice,
        MatrixLevel,
    )

    m = AllocationMatrix(
        devices=[MatrixDevice(label="Laptop", product_types=["Laptop"])],
        levels=[MatrixLevel(name="Kỹ thuật", departments=["IT"])],
        cells=[[MatrixCell(on=True, reuse_only=True)]],
    )
    s = LifecycleSettings(allocation_matrix=m)
    items, level, _ = s.items_for_employee("Kỹ sư", None, "IT")
    assert level == "Kỹ thuật" and [i.label for i in items] == ["Laptop (máy cũ)"]
    stock = returned_first(
        [{"id": "new1", "product_type": "Laptop"}, {"id": "old1", "product_type": "Laptop"}], {"old1"}
    )
    needs = [
        Need("r1", date(2026, 12, 1), None, items=items),
        Need("r2", date(2026, 12, 2), None, items=items),
    ]
    plan = allocate(needs, s.allocation_standard, stock)
    [first], [second] = plan["r1"], plan["r2"]
    assert [a["id"] for a in first.reuse] == ["old1"] and first.buy == first.wait == 0
    # Only a new laptop is left: never offered, never bought – the second hire waits for a returned one.
    assert second.reuse == [] and second.buy == 0 and second.wait == 1


def test_assets_in_repair_or_due_back_are_not_a_state_mismatch() -> None:
    requests, users, assets, bs = _data()
    assigned = next(a for a in assets if a.get("user_id"))
    for state, flagged in (("In Repair", False), ("To Be Returned", False), ("Inventory", True)):
        changed = [{**a, "state": state} if a is assigned else a for a in assets]
        notes = reconcile(requests, users, changed, bs, S, date(2026, 11, 16)).notes
        hit = [
            n for n in notes if n["rule"] == "asset_state_mismatch" and n.get("asset_id") == assigned["id"]
        ]
        assert bool(hit) is flagged, state


def test_old_pending_disposal_default_becomes_expired() -> None:
    from app.modules.lifecycle.settings import LifecycleSettings

    assert LifecycleSettings().asset_state_pending_disposal == "Expired"
    assert (
        LifecycleSettings(asset_state_pending_disposal="Chờ thanh lý").asset_state_pending_disposal
        == "Expired"
    )
    assert LifecycleSettings(asset_state_pending_disposal="Retired").asset_state_pending_disposal == "Retired"


def test_closed_and_cancelled_vietnamese_statuses():
    import unicodedata

    from app.modules.lifecycle.settings import LifecycleSettings

    s = LifecycleSettings()
    for ok in ("Đã xong", "HOÀN TẤT", unicodedata.normalize("NFD", "Đã huỷ"), "Từ chối"):
        assert s.is_closed(ok), ok
    for no in ("Chờ hoàn thành", "Đang xử lý", "", None):
        assert not s.is_closed(no), no
    assert s.is_cancelled("Đã hủy") and not s.is_cancelled("Đã xong")
    s.sdp_cancelled_statuses = [x for x in s.sdp_cancelled_statuses if x not in ("Đã huỷ", "Đã hủy")]
    assert not s.is_cancelled("Đã huỷ")


def _rq(rid, kind, day, status=None, errors=None, emp="003551"):  # type: ignore[no-untyped-def]
    return {"id": rid, "employee_id": emp, "full_name": "Nguyễn Văn An", "kind": kind, "effective_date": day,
            "created_at": day, "parse_errors": errors or [], "status": status}  # fmt: skip


def test_cancelled_request_does_not_decide_kind() -> None:
    _, users, assets, bs = _data()
    run = lambda reqs: reconcile(reqs, users, assets, bs, S, date(2026, 11, 16))  # noqa: E731
    rules, row = _issues(
        run(
            [
                _rq("1", "onboard", "2026-11-02", "Đã xong", emp="002100"),
                _rq("2", "offboard", "2026-11-10", "Đã huỷ", emp="002100"),
            ]
        ),
        "002100",
    )
    assert row.kind == "onboard" and not any(r.startswith("offboard_") for r in rules)
    assert row.request_ids == ["1", "2"]
    rules, row = _issues(run([_rq("2", "offboard", "2026-11-10", "Đã xong", emp="002100")]), "002100")
    assert row.kind == "offboard" and "offboard_biostar_active" in rules
    rules, row = _issues(run([_rq("2", "offboard", "2026-11-10", "Đã huỷ", emp="002100")]), "002100")
    assert row.kind is None and row.cells["sdp"]["label"] == "RTR đã huỷ"


def test_closed_request_parse_errors_are_not_reported() -> None:
    _, users, assets, bs = _data()
    err = ["Thiếu Mã nhân viên"]
    for status, reported in (("Đã xong", False), ("Đang xử lý", True)):
        by_emp = reconcile(
            [_rq("1", "onboard", "2026-11-02", status, err, emp="002100")],
            users,
            assets,
            bs,
            S,
            date(2026, 11, 16),
        )
        rules, _ = _issues(by_emp, "002100")
        assert ("request_parse_error" in rules) is reported
        orphan = {**_rq("3", None, "2026-11-02", status, err), "employee_id": None}
        res = reconcile([orphan], users, assets, bs, S, date(2026, 11, 16))
        assert any(n["rule"] == "request_parse_error" for n in res.notes) is reported


def _bs(uid, name="Nguyễn Văn An", active=True, retired=False):  # type: ignore[no-untyped-def]
    return {"user_id": uid, "name": name, "active": active, "disabled": not active, "retired": retired,
            "cards": 1, "fingerprints": 0, "faces": 0}  # fmt: skip


def _cancelled_onboard_rules(reqs, bs, users=(), assets=(), sources=None, emp="003551"):  # type: ignore[no-untyped-def]
    res = reconcile(reqs, list(users), list(assets), bs, S, date(2026, 11, 16), sources)
    rules, row = _issues(res, emp)
    return {r for r in rules if r.startswith("onboard_cancelled")}, row


def test_cancelled_onboard_with_access_or_assets_still_granted() -> None:
    cancelled = [_rq("7", "onboard", "2026-11-02", "Đã huỷ")]
    users = [{"id": "u1", "name": "Nguyễn Văn An", "employee_id": "003551", "department": None}]
    in_use = [{"id": "a1", "user_id": "u1", "state": S.asset_state_in_use, "name": "PC", "asset_tag": "T1"}]
    bs = [_bs("003551")]
    rules, row = _cancelled_onboard_rules(cancelled, bs, users, in_use)
    assert rules == {"onboard_cancelled_biostar_active", "onboard_cancelled_assets_in_use"}
    by = {i.rule: i for i in row.issues}
    assert by["onboard_cancelled_biostar_active"].severity == "high"
    assert by["onboard_cancelled_assets_in_use"].severity == "medium"
    assert "#7 (tạo 02/11/2026)" in by["onboard_cancelled_biostar_active"].message
    # sources not read -> not judged
    src = {"sdp": True, "assets": True, "biostar": False}
    assert _cancelled_onboard_rules(cancelled, bs, users, in_use, src)[0] == {
        "onboard_cancelled_assets_in_use"
    }
    src = {"sdp": True, "assets": False, "biostar": True}
    assert _cancelled_onboard_rules(cancelled, bs, users, in_use, src)[0] == {
        "onboard_cancelled_biostar_active"
    }


def test_cancelled_onboard_rules_do_not_fire_when_unsafe() -> None:
    bs = [_bs("003551")]
    cancelled = _rq("7", "onboard", "2026-11-02", "Đã huỷ")
    # another onboard is done
    done = _rq("6", "onboard", "2026-11-01", "Đã xong")
    assert not _cancelled_onboard_rules([done, cancelled], bs)[0]
    # wrong ID typed: names differ
    assert not _cancelled_onboard_rules([cancelled], [_bs("003551", "Lê Văn Cường")])[0]
    # retired BioStar user
    assert not _cancelled_onboard_rules([cancelled], [_bs("003551", retired=True)])[0]
    # a live request of unclassified kind (C-37) and a cancelled offboard
    live = _rq("8", None, "2026-11-03", "Đang xử lý")
    assert not _cancelled_onboard_rules([cancelled, live], bs)[0]
    assert not _cancelled_onboard_rules([_rq("9", "offboard", "2026-11-02", "Đã huỷ")], bs)[0]


def test_cancelled_onboard_finds_unclaimed_biostar_user_by_id_digits() -> None:
    cancelled = [_rq("7", "onboard", "2026-11-02", "Đã huỷ")]
    # SDP has employee IDs (so BioStar users are not employees themselves) but none for this person
    users = [normalize.sdp_user(u, "employee_id") for u in SDP_USERS if u.get("employee_id") != "003551"]
    rules, _ = _cancelled_onboard_rules(cancelled, [_bs("3551")], users)
    assert rules == {"onboard_cancelled_biostar_active"}
    # ambiguous: two unclaimed candidates
    assert not _cancelled_onboard_rules(cancelled, [_bs("3551"), _bs("103551")], users)[0]


def _other_users():  # type: ignore[no-untyped-def]
    return [normalize.sdp_user(u, "employee_id") for u in SDP_USERS if u.get("employee_id") != "003551"]


def test_cancelled_onboard_fallback_rejects_candidates_claimed_by_others() -> None:
    cancelled = [_rq("7", "onboard", "2026-11-02", "Đã huỷ")]
    users = _other_users()
    # key "3551" already belongs to another SDP user
    owner = {"id": "u9", "name": "Khác", "employee_id": "3551", "department": None}
    assert not _cancelled_onboard_rules(cancelled, [_bs("3551")], [*users, owner])[0]
    # ... or to another request
    other_req = _rq("8", "onboard", "2026-11-02", "Đã xong", emp="3551")
    assert not _cancelled_onboard_rules([*cancelled, other_req], [_bs("3551")], users)[0]


def test_cancelled_onboard_assets_need_one_user_with_same_name() -> None:
    cancelled = [_rq("7", "onboard", "2026-11-02", "Đã huỷ")]
    in_use = [{"id": "a1", "user_id": "u1", "state": S.asset_state_in_use, "name": "PC", "asset_tag": "T1"}]
    mk = lambda uid, name: {"id": uid, "name": name, "employee_id": "003551", "department": None}  # noqa: E731
    assert not _cancelled_onboard_rules(cancelled, [], [mk("u1", "Lê Văn Cường")], in_use)[0]
    dup = [mk("u1", "Nguyễn Văn An"), mk("u2", "Nguyễn Văn An")]
    assert "onboard_cancelled_assets_in_use" not in _cancelled_onboard_rules(cancelled, [], dup, in_use)[0]


def test_cancelled_onboard_fallback_filters_before_counting() -> None:
    cancelled = [_rq("7", "onboard", "2026-11-02", "Đã huỷ")]
    users = _other_users()
    # inactive exact match and no twin
    assert not _cancelled_onboard_rules(cancelled, [_bs("003551", active=False)], users)[0]
    # inactive exact + active twin with the same name
    bs = [_bs("003551", active=False), _bs("3551")]
    assert _cancelled_onboard_rules(cancelled, bs, users)[0] == {"onboard_cancelled_biostar_active"}
    # retired twin does not make the match ambiguous
    bs = [_bs("3551"), _bs("103551", retired=True)]
    assert _cancelled_onboard_rules(cancelled, bs, users)[0] == {"onboard_cancelled_biostar_active"}
    # SDP without any employee ID
    assert _cancelled_onboard_rules(cancelled, [_bs("3551")], [])[0] == {"onboard_cancelled_biostar_active"}


def test_cancelled_onboard_fallback_row_has_name_and_no_unknown_note() -> None:
    cancelled = [_rq("7", "onboard", "2026-11-02", "Đã huỷ")]
    res = reconcile(cancelled, _other_users(), [], [_bs("3551")], S, date(2026, 11, 16))
    assert res.notes == [] or all(n.get("employee_id") != "3551" for n in res.notes)
    _, row = _issues(res, "003551")
    assert row.full_name == "Nguyễn Văn An"


def test_cancelled_onboard_label_uses_vietnam_date() -> None:
    cancelled = [{**_rq("7", "onboard", "2026-11-02", "Đã huỷ"), "created_at": "2026-11-01T18:30:00+00:00"}]
    _, row = _cancelled_onboard_rules(cancelled, [_bs("003551")])
    assert "(tạo 02/11/2026)" in row.issues[0].message


# ---------------------------------------------------------------- SDP ID suggestions
def _sdpu(name, emp=None, uid="1", dept=None):  # type: ignore[no-untyped-def]
    return {"id": uid, "name": name, "email": f"{uid}@x.vn", "employee_id": emp, "department": dept}


def _bsu(name, uid, **kw):  # type: ignore[no-untyped-def]
    return {"user_id": uid, "name": name, "source": "BS", "active": True, **kw}


def _one(sdp_name, *bs, others=()):  # type: ignore[no-untyped-def]
    from app.modules.lifecycle.id_suggest import suggest

    (row,) = suggest([_sdpu(sdp_name), *others], list(bs))
    return row


@pytest.mark.parametrize(
    ("sdp", "bio"),
    [("Nguyễn Văn An", "NGUYEN VAN AN"), ("An. Nguyen Van", "Nguyễn Văn An")],
)
def test_suggest_exact_ignores_accents_and_order(sdp: str, bio: str) -> None:
    row = _one(sdp, _bsu(bio, "003551"))
    assert row["status"] == "matched"
    assert row["suggestion"]["employee_id"] == "03551"  # last 5 digits, never padded to 6
    assert row["suggestion"]["biostar_user_id"] == "003551"


@pytest.mark.parametrize(
    ("sdp", "bio"),
    [("Nguyễn Thị Ngọc Hà", "Nguyễn Thị Thu Hà"), ("Lê Thị Thu Hà", "Nguyễn Thị Thu Hà")],
)
def test_suggest_different_person_is_not_matched(sdp: str, bio: str) -> None:
    assert _one(sdp, _bsu(bio, "1001"))["status"] == "none"


def test_suggest_accent_difference_between_accented_names_is_near() -> None:
    row = _one("Nguyễn Văn Hùng", _bsu("Nguyễn Văn Hưng", "1001"))
    assert row["status"] == "near" and "khác dấu" in row["suggestion"]["notes"]


@pytest.mark.parametrize(
    ("sdp", "bio"), [("Nguyễn Thị Hà", "Nguyễn Thị Thu Hà"), ("Ha. Nguyen Thi Thu", "Nguyễn Thị Hà")]
)
def test_suggest_missing_middle_name_is_near(sdp: str, bio: str) -> None:
    assert _one(sdp, _bsu(bio, "1001"))["status"] == "near"


def test_suggest_ambiguous_claimed_and_ranking() -> None:
    assert (
        _one("Nguyễn Văn An", _bsu("Nguyễn Văn An", "1"), _bsu("Nguyễn Văn An", "2"))["status"] == "ambiguous"
    )
    row = _one("Nguyễn Văn An", _bsu("Nguyễn Văn An", "100008"), others=[_sdpu("Khác", "000008", uid="9")])
    assert row["status"] == "claimed" and row["suggestion"] is None
    assert "Mã đã thuộc user SDP khác" in row["candidates"][0]["notes"][0]
    row = _one(
        "Nguyễn Văn An",
        _bsu("Nguyễn Văn An", "1001", active=False),
        _bsu("Nguyễn Văn An", "1002", retired=True, active=False),
        _bsu("Nguyễn Văn An", "1003"),
    )
    assert row["status"] == "matched" and row["suggestion"]["biostar_user_id"] == "1003"
    assert row["candidates"][0]["biostar_user_id"] == "1003"


def test_suggest_junk_ids_are_missing() -> None:
    from app.modules.lifecycle.id_suggest import missing

    assert all(missing(v) for v in (None, "", "N/A", "-", "000000", "1"))
    assert not missing("003551") and not missing("3551")
    assert _one("Nguyễn Văn An")["status"] == "none"


def test_suggest_same_top_for_two_sdp_users_is_ambiguous() -> None:
    from app.modules.lifecycle.id_suggest import suggest

    rows = suggest(
        [_sdpu("Nguyễn Văn An", uid="1"), _sdpu("Nguyen Van An", uid="2")], [_bsu("NGUYEN VAN AN", "1001")]
    )
    assert [r["status"] for r in rows] == ["ambiguous", "ambiguous"]
    assert all(
        r["suggestion"] is None and "Cùng ứng viên với user SDP" in r["candidates"][0]["notes"][-1]
        for r in rows
    )


def test_suggest_inactive_or_retired_top_is_near() -> None:
    assert _one("Nguyễn Văn An", _bsu("Nguyễn Văn An", "1001", active=False))["status"] == "near"
    assert _one("Nguyễn Văn An", _bsu("Nguyễn Văn An", "1001", retired=True))["status"] == "near"


def test_suggest_exact_any_word_order_and_whitespace_not_accent_diff() -> None:
    row = _one("An Van Nguyen", _bsu("NGUYEN  VAN AN ", "1001"))
    assert row["status"] == "matched" and row["suggestion"]["notes"] == []
    assert _one("Nguyễn Văn An", _bsu("Nguyễn  Văn An ", "1001"))["status"] == "matched"
    assert _one("Nguyễn An An", _bsu("Nguyễn An", "1001"))["status"] == "none"


def test_suggest_junk_values_are_missing() -> None:
    from app.modules.lifecycle.id_suggest import missing

    assert all(missing(v) for v in ("na", "NA", "n/a", "None", "null", "TBD", "x", "-"))


def test_microsoft_settings_defaults_and_validation() -> None:
    from pydantic import ValidationError

    s = LifecycleSettings()
    assert s.microsoft_enabled is False and s.ad_ou_map == [] and s.sam_max_length == 20
    assert s.naming_order == ["short", "short2", "given_family", "given_full", "short3"]
    assert s.usage_location == "VN" and (s.entra_wait_hours, s.mailbox_wait_hours) == (3, 2)
    ok = LifecycleSettings(
        upn_domain="corp.vn", ad_base_ou="OU=Users,DC=corp,DC=local",
        ad_ou_map=[{"department": "IT", "ou": "OU=IT,OU=Users,DC=corp,DC=local"}],
    )  # fmt: skip
    assert ok.ad_ou_map[0].unit == ""
    for bad in (
        {"upn_domain": "not a domain"}, {"ad_ban_ou": "Ban_Users"}, {"naming_order": ["bogus"]},
        {"sam_max_length": 21}, {"usage_location": "VNM"}, {"ad_ou_map": [{"ou": "x"}]},
    ):  # fmt: skip
        with pytest.raises(ValidationError):
            LifecycleSettings(**bad)


def test_matrix_license_software_validated_and_ignored_by_allocation() -> None:
    from pydantic import ValidationError

    from app.modules.lifecycle.allocation import Need, allocate
    from app.modules.lifecycle.settings import AllocationMatrix, MatrixCell, MatrixDevice, MatrixLevel

    guid = "05e9a617-0261-4cee-bb44-138d3ef5d965"
    with pytest.raises(ValidationError):
        MatrixLevel(name="A", license_skus=["not-a-guid"])

    def run(**extra: object) -> tuple[list[tuple[str, int]], object]:
        m = AllocationMatrix(
            devices=[MatrixDevice(label="Laptop", product_types=["Laptop"])],
            levels=[MatrixLevel(name="A", **extra)],  # type: ignore[arg-type]
            cells=[[MatrixCell(on=True)]],
        )
        s = LifecycleSettings(allocation_matrix=m)
        items, _, _ = s.items_for_employee(None, None)
        plan = allocate([Need("r1", date(2026, 12, 1), None, items=items)], s.allocation_standard, [])
        return [(i.label, i.quantity) for i in s.allocation_standard], plan

    base = run()
    with_ms = run(license_skus=[guid], license_names=["M365 E3"], software=["Word", "Excel"])
    assert base[0] == with_ms[0] and repr(base[1]) == repr(with_ms[1])


def test_job_params_new_keys_and_invalid_stored_settings_fall_back() -> None:
    from types import SimpleNamespace

    from app.modules.lifecycle.jobs_api import job_params

    req = SimpleNamespace(
        kind="onboard", id="1", display_id="2", effective_date=None, employee_id="003551", full_name="A",
        fields={"department": "Kinh Doanh", "unit": "HCM 2", "division": "Khối KD", "phone": "0900",
                "manager": "Trần Văn B (001200)"},
    )  # fmt: skip
    p = job_params(req)  # type: ignore[arg-type]
    e = p["employee"]
    assert e["department"] == "Kinh Doanh – HCM 2" and e["department_only"] == "Kinh Doanh"
    assert (e["unit"], e["division"], e["phone"], e["manager_employee_id"]) == (
        "HCM 2",
        "Khối KD",
        "0900",
        "001200",
    )
    assert p["options"] == {"mailbox_handover": False}
    req.fields = {"manager": "Trần Văn B (000012)"}
    assert job_params(req)["employee"]["manager_employee_id"] == "000012"  # type: ignore[arg-type]


def test_account_username_candidates() -> None:
    from app.modules.lifecycle.accounts import pick_username, username_candidates

    order = S.naming_order
    want = ["binh.nt", "binh.nt2", "binh.nguyen", "binh.nguyenthanh", "binh.nt3"]
    assert username_candidates("Nguyễn Thanh Bình", order) == want
    assert username_candidates("Bình. Nguyễn Thanh", order) == want
    assert username_candidates("Đặng Thị Ánh Đào", order)[:3] == ["dao.dta", "dao.dta2", "dao.dang"]
    assert username_candidates("Bình", order) == ["binh", "binh2", "binh3"]
    long = username_candidates("Nguyễn Thị Thanh Hoàng Phương", order)
    assert long and all(len(c) <= 20 for c in long) and "phuong.nguyenthithanhhoang" not in long
    assert pick_username(want, lambda c: c in {"binh.nt", "binh.nt2"}) == "binh.nguyen"
    assert pick_username(want, lambda c: True) is None


def test_account_upn_ou_entitlements() -> None:
    from app.modules.lifecycle.accounts import entitlements, ou_for, upn
    from app.modules.lifecycle.settings import MatrixLevel, OuRule

    assert upn("Binh.NT", "Corp.Local") == "binh.nt@corp.local"
    with pytest.raises(ValueError):
        upn("binh", "")
    dn = lambda n: f"OU={n},DC=c,DC=l"  # noqa: E731
    s = LifecycleSettings(
        ad_default_ou=dn("Def"),
        ad_ou_map=[
            OuRule(division="Khối A", ou=dn("Div")),
            OuRule(department="Kinh Doanh", ou=dn("Dept")),
            OuRule(division="Khối A", department="Kinh Doanh", ou=dn("DivDept")),
            OuRule(division="Khối A", department="Kinh Doanh", unit="Nhóm 1", ou=dn("Unit")),
        ],
    )
    emp = {"division": "KHỐI a", "department": "kinh doanh", "unit": "Nhóm 1"}
    assert ou_for(emp, s)[0] == dn("Unit")
    assert ou_for({**emp, "unit": "X"}, s)[0] == dn("DivDept")
    assert ou_for({"division": "Khác", "department": "Kinh Doanh"}, s)[0] == dn("Dept")
    assert ou_for({"division": "Khối A"}, s)[0] == dn("Div")
    assert ou_for({"division": "Z"}, s) == (dn("Def"), "OU mặc định")
    assert ou_for({}, LifecycleSettings()) == (None, "")
    assert entitlements(None) == {"license_skus": [], "license_names": [], "software": []}
    g = "11111111-2222-3333-4444-555555555555"
    lv = MatrixLevel(name="L", license_skus=[g], license_names=["E3"], software=["Word"])
    assert entitlements(lv) == {"license_skus": [g], "license_names": ["E3"], "software": ["Word"]}
    with pytest.raises(ValueError):
        MatrixLevel(name="L", license_names=["E3"])  # names without matching SKUs


def test_ou_for_matches_department_only_from_real_job_params() -> None:
    from types import SimpleNamespace

    from app.modules.lifecycle.accounts import ou_for
    from app.modules.lifecycle.jobs_api import job_params
    from app.modules.lifecycle.settings import OuRule

    req = SimpleNamespace(
        kind="onboard", id="1", display_id="2", effective_date=None, employee_id="003551", full_name="A",
        fields={"department": "Kinh Doanh", "unit": "HCM 2", "division": "Khối KD"},
    )  # fmt: skip
    s = LifecycleSettings(ad_ou_map=[OuRule(department="Kinh Doanh", ou="OU=KD,DC=c,DC=l")])
    emp = job_params(req)["employee"]  # type: ignore[arg-type]
    assert emp["department"] == "Kinh Doanh – HCM 2"
    assert ou_for(emp, s)[0] == "OU=KD,DC=c,DC=l"


def test_name_parts_empty_and_settings_microsoft_validation() -> None:
    from app.modules.lifecycle.accounts import name_parts, username_candidates
    from app.modules.lifecycle.settings import OuRule

    for blank in ("", "   "):
        assert name_parts(blank) == ("", [])
        assert username_candidates(blank, S.naming_order) == []
    base = "OU=Users,DC=corp,DC=local"
    with pytest.raises(ValueError):
        LifecycleSettings(microsoft_enabled=True, upn_domain="corp.local")
    with pytest.raises(ValueError):
        LifecycleSettings(microsoft_enabled=True, ad_base_ou=base)
    LifecycleSettings(microsoft_enabled=True, ad_base_ou=base, upn_domain="corp.local")
    ok = f"OU=IT,{base}"
    LifecycleSettings(
        ad_base_ou=base, ad_default_ou=ok, ad_ban_ou=ok, ad_ou_map=[OuRule(ou=ok, department="x")]
    )
    for kw in (
        {"ad_default_ou": "OU=X,DC=corp,DC=local"},
        {"ad_ban_ou": "OU=Ban,DC=corp,DC=local"},
        {"ad_ou_map": [OuRule(ou="OU=X,DC=corp,DC=local", department="x")]},
        {"ad_default_ou": f"CN=a\\,{base}"},  # escaped comma: not inside base
        {"ad_retained_ou": "OU=X,DC=corp,DC=local"},  # outside the base
        {"ad_retained_ou": ok, "ad_ban_ou": ok},  # same as Ban_Users
        {"ad_retained_ou": ok, "ad_ban_ou": f"OU=Old,{ok}"},  # nested in each other
        {"mailbox_retention_days": 0},
    ):
        with pytest.raises(ValueError):
            LifecycleSettings(ad_base_ou=base, **kw)  # type: ignore[arg-type]
    assert LifecycleSettings().mailbox_retention_days == 90
    LifecycleSettings(ad_base_ou=base, ad_retained_ou=ok, ad_ban_ou=f"OU=Ban,{base}")


def _transfer_settings() -> LifecycleSettings:
    from app.modules.lifecycle.settings import OuRule

    g = lambda n: f"CN={n},OU=Groups,DC=c,DC=l"  # noqa: E731
    return LifecycleSettings(
        ad_default_ou="OU=Def,DC=c,DC=l",
        biostar_access_group_ids=["1"],
        ad_ou_map=[
            OuRule(division="KD", groups=[g("All"), g("Shared")], biostar_access_group_ids=["10"]),
            OuRule(department="Sales", ou="OU=Sales,DC=c,DC=l", groups=[g("Sales"), g("Shared")]),
            OuRule(department="Sales", unit="HCM", groups=[g("SalesHCM")], biostar_access_group_ids=["11"]),
            OuRule(department="Ops", ou="OU=Ops,DC=c,DC=l", groups=[g("Ops"), g("Shared")]),
        ],
    )


def test_transfer_parse_new_and_old_labels_and_onboard_unchanged() -> None:
    s = LifecycleSettings(
        transfer_new_labels={"department": ["Phòng mới"], "job_title": ["Chức danh mới"]},
        transfer_old_labels={"department": ["Phòng cũ"]},
    )
    body = "Loại biến động: Thuyên chuyển<br>Phòng cũ: Ops<br>Phòng mới: Sales<br>Phòng: Ops<br>Khối: KD"
    p = parse_rtr_description(body, s)
    assert p.kind == "transfer"
    assert p.fields["department"] == "Sales" and p.fields["job_title"] is None  # configured: own labels only
    assert p.fields["division"] == "KD"  # not configured: normal label
    assert p.old_fields == {"department": "Ops"}
    p = parse_rtr_description(body.replace("Thuyên chuyển", "Nhân viên mới"), s)
    assert p.kind == "onboard" and p.fields["department"] == "Ops" and p.old_fields == {}


def test_transfer_job_params_keep_only_new_values() -> None:
    from types import SimpleNamespace

    from app.modules.lifecycle.jobs_api import job_params

    req = SimpleNamespace(
        kind="transfer", id="1", display_id="2", effective_date=None, employee_id="003551", full_name="A",
        fields={"manager": "B (001200)", "department": None, "old": {"department": "Ops", "manager": "C (000999)"}},
    )  # fmt: skip
    p = job_params(req)  # type: ignore[arg-type]
    assert p["employee"] == {"id": "003551", "full_name": "A", "manager_employee_id": "001200"}
    assert p["employee_old"] == {
        "department": "Ops",
        "manager": "C (000999)",
        "manager_employee_id": "000999",
    }
    assert p["sources"] == {
        "manager": "request",
        "old.department": "request_old",
        "old.manager": "request_old",
    }


def test_transfer_plan_pure_diff() -> None:
    from app.modules.lifecycle import transfer as t

    s = _transfer_settings()
    g = lambda n: f"CN={n},OU=Groups,DC=c,DC=l"  # noqa: E731
    only_manager = {"id": "1", "manager_employee_id": "000012"}
    assert t.target_ou(only_manager, s) is None and t.target_groups(only_manager, s) is None
    assert t.group_changes([g("Sales")], only_manager, s) == {"add": [], "remove": []}
    assert t.biostar_groups(["5"], only_manager, s) == (["5"], False)
    # same department, different unit: department-level groups stay, unit group changes
    new = {"division": "KD", "department": "Sales – HCM", "department_only": "Sales", "unit": "HCM"}
    cur = [g("All"), g("Sales"), g("Shared"), g("Other"), g("Ops").lower()]
    ch = t.group_changes(cur, new, s)
    assert ch["add"] == [g("SalesHCM")]
    assert ch["remove"] == [g("Ops").lower()]  # shared kept, unmanaged "Other" kept, DN compare ignores case
    assert t.target_ou(new, s) == "OU=Sales,DC=c,DC=l"  # rule without ou does not win
    # biostar: unmanaged id 99 kept, managed 10 not in target (no KD rule match) dropped
    out, changed = t.biostar_groups(["1", "10", "99"], {"department_only": "Sales", "unit": "HCM"}, s)
    assert changed and out == ["1", "99", "11"]
    assert t.biostar_groups(["1", "99", "11"], {"department_only": "Sales", "unit": "HCM"}, s)[1] is False
    # attributes
    assert t.attribute_changes(
        {"title": "X"}, {"job_title": "X", "department": "", "manager_employee_id": "7"}
    ) == {"manager_employee_id": "7"}
    assert len(t.attribute_changes({}, {"department": "D" * 80})["department"]) == 64


def test_transfer_license_changes() -> None:
    from app.modules.lifecycle import transfer as t
    from app.modules.lifecycle.settings import AllocationMatrix, MatrixLevel

    a, b, other = (f"11111111-2222-3333-4444-55555555555{i}" for i in (1, 2, 3))
    lv_a = MatrixLevel(name="A", titles=["a"], license_skus=[a])
    lv_b = MatrixLevel(name="B", titles=["b"], license_skus=[b])
    m = AllocationMatrix(levels=[lv_a, lv_b, MatrixLevel(name="Z")], devices=[], cells=[[], [], []])
    assert t.license_changes([a, other], lv_b, m) == {"add": [b], "remove": [a]}  # unmanaged SKU kept
    assert t.license_changes([a], MatrixLevel(name="Z"), m) == {"add": [], "remove": []}
    assert t.license_changes([a], None, m) == {"add": [], "remove": []}
