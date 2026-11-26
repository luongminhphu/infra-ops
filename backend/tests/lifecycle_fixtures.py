"""Fake SDP / BioStar payloads (fictional people) shared by lifecycle tests."""

from __future__ import annotations

from typing import Any


def rtr_html(
    emp: str, name: str, change: str, eff: str, dept: str = "Kinh Doanh", manager: str = "Trần Văn B (001200)"
) -> str:
    return (
        "<div>Vui lòng mô tả yêu cầu:<br>Loại biến động: " + change + "<br><br>"
        "A. Thông tin nhân viên<br>"
        f"1. Họ &amp; tên: {name}<br>"
        f"2. Mã nhân viên: {emp}<br>"
        f"3. Ngày hiệu lực: {eff}<br>"
        "4. Chức danh nội bộ: Nhân Viên Tư Vấn (Account Executive)<br>"
        "5. Chức danh bên ngoài: Chuyên Viên Tư Vấn<br>"
        "6. Khối: Kinh Doanh Khách Hàng Cá Nhân<br>"
        f"7. Phòng: {dept}<br>"
        "8. Bộ phận: Vùng Kinh Doanh - HCM 2<br>"
        "9. Nơi làm việc: Chi nhánh 1<br>"
        "10. Số điện thoại: 0900000000<br>"
        f"11. Quản lý trực tiếp: {manager}<br>"
        "12. Quản lý gián tiếp: Lê Thị C<br>"
        "13. Band: B3<br><br>"
        "B. Thiết lập HR<br>1. Nhân viên môi giới: Có<br>4. Ghi chú: null</div>"
    )


def sdp_ok(collection: str, rows: list[dict[str, Any]], has_more: bool = False) -> dict[str, Any]:
    return {
        "response_status": [{"status_code": 2000, "status": "success"}],
        "list_info": {"has_more_rows": has_more, "row_count": len(rows)},
        collection: rows,
    }


def ms(day: int) -> dict[str, str]:
    # 2026-11-<day> 00:00 UTC in epoch ms
    from datetime import UTC, datetime

    return {"value": str(int(datetime(2026, 11, day, tzinfo=UTC).timestamp() * 1000))}


REQUESTS = [
    {
        "id": "1001",
        "display_id": "7901",
        "subject": "(RTR-634) Nhân viên mới/Nguyễn Văn An/Kinh Doanh",
        "status": {"name": "Open"},
        "created_time": ms(1),
        "last_updated_time": ms(2),
    },
    {
        "id": "1002",
        "display_id": "7902",
        "subject": "(RTR-635) Nghỉ việc/Phạm Thị Bình/Kế Toán",
        "status": {"name": "Open"},
        "created_time": ms(3),
        "last_updated_time": ms(3),
    },
    {
        "id": "1003",
        "display_id": "7903",
        "subject": "(RTR-636) ???",
        "status": {"name": "Open"},
        "created_time": ms(4),
        "last_updated_time": ms(4),
    },
]

DETAILS = {
    "1001": rtr_html("003551", "Nguyễn Văn An", "Nhân viên mới", "02/11/2026"),
    "1002": rtr_html("002100", "Phạm Thị Bình", "Nghỉ việc", "05/11/2026", dept="Kế Toán"),
    "1003": "<div>Loại biến động: Chính thức<br>1. Họ &amp; tên: Không Mã<br></div>",
}

SDP_USERS = [
    {
        "id": "u1",
        "name": "Nguyễn Văn An",
        "email_id": "an.nv@congty.local",
        "employee_id": "003551",
        "department": {"name": "Kinh Doanh"},
    },
    {
        "id": "u2",
        "name": "Phạm Thị Bình",
        "email_id": "binh.pt@congty.local",
        "employee_id": "002100",
        "job_title": "Kế toán viên",
        "department": {"name": "Kế Toán", "site": {"name": "AB Tower, 76A Le Lai"}},
    },
    {"id": "u3", "name": "Đỗ Minh Châu", "employee_id": "001500"},
]

# SDP users the paged list does not return (found only by searching their employee ID).
HIDDEN_SDP_USERS = [{"id": "u9", "name": "Hung. Ta Quoc", "employee_id": "003261"}]

ASSETS = [
    {
        "id": "a1",
        "name": "HSC-LT-0123",
        "asset_tag": "LT0123",
        "serial_number": "SN1",
        "product": {"name": "Dell Latitude 5440", "product_type": {"name": "Laptop"}},
        "state": {"name": "In Use"},
        "user": {"id": "u2", "name": "Phạm Thị Bình"},
    },
    {
        "id": "a2",
        "name": "HSC-MN-0001",
        "asset_tag": "MN0001",
        "product": {"name": "Dell P2422H", "product_type": {"name": "Monitor"}},
        "state": {"name": "In Store"},
    },
    {
        "id": "a4",
        "name": "HSC-PC-0001",
        "asset_tag": "PC0001",
        "product": {"name": "Dell OptiPlex 7010", "product_type": {"name": "Workstation"}},
        "state": {"name": "In Store"},
    },
    {
        "id": "a3",
        "name": "Printer-01",
        "asset_tag": "PR01",
        "product": {"name": "HP", "product_type": {"name": "Printer"}},
        "state": {"name": "In Use"},
        "user": {"id": "u3", "name": "Đỗ Minh Châu"},
    },
]

WORKSTATIONS = [
    {"id": "a1", "name": "HSC-LT-0123"},  # also in the asset list: counted once
    {
        "id": "w1",
        "name": "2212-0228.hsc.com.vn",
        "asset_tag": None,
        "computer_system": {"service_tag": "5CD2"},
        "product": {"name": "HP ProBook 440", "product_type": {"name": "Laptop"}},
        "state": {"name": "In Use"},
        "user": {"id": "u9", "name": "Hung. Ta Quoc"},
    },
]

BIOSTAR_USERS = [
    {
        "user_id": "002100",
        "name": "Pham Thi Binh",
        "disabled": "false",
        "expiry_datetime": "2030-12-31T23:59:00.00Z",
        "user_group_id": {"name": "Kế Toán"},
        "access_groups": [{"name": "Văn phòng HCM"}],
        "card_count": "1",
        "fingerprint_template_count": "2",
    },
    {
        "user_id": "009999",
        "name": "Người Lạ",
        "disabled": "false",
        "expiry_datetime": "2030-12-31T23:59:00.00Z",
    },
    {"user_id": "001500", "name": "Đỗ Minh Châu", "disabled": "true"},
    # Office ID 103261 = SDP 003261, a user the SDP list skipped.
    {
        "user_id": "103261",
        "name": "TA QUOC HUNG",
        "disabled": "false",
        "expiry_datetime": "2030-12-31T23:59:00.00Z",
    },
    {
        "user_id": "880001",
        "name": "Vendor Guard",
        "disabled": "false",
        "expiry_datetime": "2030-12-31T23:59:00.00Z",
        "user_group_id": {"name": "3. VENDOR"},
    },
]
