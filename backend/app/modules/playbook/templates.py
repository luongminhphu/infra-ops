"""Built-in playbook templates: a new guide can start from one and is then edited like any draft.

Company-specific rules (naming, OU, licences, software list) are written as examples marked "Điều chỉnh" – the
Manager adapts them before publishing. AD / Exchange / M365 steps are manual guidance: that integration is deferred,
so the app never writes to Microsoft systems."""

from __future__ import annotations

from typing import Any

ADJUST = "> **Điều chỉnh theo quy định công ty trước khi xuất bản.**"


def _step(title: str, note: str, children: list[tuple[str, str]] | None = None, tools: list[tuple[str, str]] | None = None,
          kind: str = "step", **extra: Any) -> dict[str, Any]:  # fmt: skip
    return {
        "kind": kind,
        "title": title,
        "note": note,
        "children": [{"label": label, "to": to} for label, to in (children or [])],
        "actions": [{"tool": tool, "label": label} for tool, label in (tools or [])],
        **extra,
    }


def _fact(key: str, param: str = "") -> dict[str, Any]:
    return {"fact": key, "param": param, "value": None}


def _value(v: str) -> dict[str, Any]:
    return {"fact": None, "param": "", "value": v}


ONBOARD: dict[str, Any] = {
    "title": "Onboard nhân viên mới",
    "category": "Vòng đời nhân sự",
    "description": "Từ request RTR trên SDP đến bàn giao: đối soát, tài khoản AD, mailbox, license M365, phần mềm, "
    "thiết bị, ra vào và biên bản.",
    "tree": {
        "root": "receive",
        "nodes": {
            "receive": _step(
                "Tiếp nhận request RTR trên SDP",
                "Request *Nhân viên mới* / *Tân tuyển – TV* / *Thực tập sinh* từ RTR được đồng bộ về infra-ops và tạo "
                "**Job onboard nháp**.\n\n"
                "- Nhận Job trên trang chính (hàng đợi team) hoặc trong Job\n"
                "- Mở request trên SDP, đọc đủ nội dung HR gửi\n"
                "- Ngày hiệu lực còn bao lâu? Dưới 3 ngày làm việc thì báo trưởng nhóm",
                [("", "complete")],
                [("lifecycle.requests", "Xem request RTR"), ("lifecycle.jobs", "Mở Job onboard")],
            ),
            "complete": _step(
                "Request có đủ thông tin?",
                "Bắt buộc: **Mã NV**, họ tên, phòng ban, chức danh, **band**, ngày hiệu lực, quản lý trực tiếp, "
                "địa điểm làm việc.\n\nTrang Request RTR đánh dấu dòng thiếu/sai thông tin.",
                [("Đủ", "reconcile"), ("Thiếu / sai", "ask_hr")],
                [("lifecycle.requests", "Kiểm tra lỗi đọc request")],
            ),
            "ask_hr": _step(
                "Yêu cầu HR bổ sung",
                "Ghi chú trên request SDP các trường còn thiếu, gán lại cho HR (người tạo RTR). Không tự đoán Mã NV "
                "hay band.\n\nKhi HR cập nhật, đồng bộ lại rồi kiểm tra lần nữa.",
                [("Đã bổ sung", "complete")],
            ),
            "reconcile": _step(
                "Đối soát thông tin nhân viên",
                "Tìm Mã NV trên trang Đối soát:\n\n"
                "- Chưa có user SDP: bình thường với nhân viên mới (tài khoản AD sẽ đồng bộ sang SDP)\n"
                "- Đã có user BioStar còn hoạt động / đã có tài sản: có thể là **tái tuyển** hoặc trùng Mã NV\n"
                "- Họ tên khác giữa các hệ thống: xác nhận với HR",
                [("", "rehire")],
                [("lifecycle.reconciliation", "Đối soát theo Mã NV")],
            ),
            "rehire": _step(
                "Nhân viên đã có laptop đang dùng?",
                "App tự kiểm tra từ lần đối soát gần nhất.",
                [("Đúng", "rehire_confirm"), ("Sai", "ad_account")],
                kind="check",
                check={"left": _fact("lifecycle.assets_in_use", "Laptop"), "op": ">=", "right": _value("1")},
            ),
            "rehire_confirm": _step(
                "Xác nhận tái tuyển / trùng Mã NV với HR",
                "Đã có laptop gán cho Mã NV này. Xác nhận với HR:\n\n"
                "- **Tái tuyển / chuyển đổi**: dùng lại tài khoản AD cũ (kích hoạt lại), không cấp thiết bị mới\n"
                "- **Trùng Mã NV**: dừng, báo HR sửa Mã NV trên RTR",
                [("Tái tuyển – dùng lại tài khoản", "mailbox"), ("Trùng Mã NV – dừng", "stop_duplicate")],
            ),
            "stop_duplicate": _step(
                "Dừng: Mã NV trùng",
                "Ghi chú trên request SDP, chuyển lại HR. Huỷ Job onboard nếu HR tạo request mới.",
                kind="outcome",
                outcome={"result": "exception", "approval": "HR xác nhận Mã NV đúng"},
            ),
            "ad_account": _step(
                "Tạo tài khoản AD",
                f"{ADJUST}\n\n"
                "Làm thủ công trên AD (tích hợp Microsoft đang tạm hoãn):\n\n"
                "1. Tên đăng nhập: `ten.hodem` không dấu (trùng thì thêm số), UPN `@congty.com.vn`\n"
                "2. Display name: `Tên. Họ Đệm` – giống quy ước trên SDP\n"
                "3. OU theo phòng ban, điền **Employee ID = Mã NV**, chức danh, phòng ban, quản lý\n"
                "4. Mật khẩu tạm, bắt đổi khi đăng nhập lần đầu\n"
                "5. Thêm vào nhóm bảo mật / thư mục share của phòng ban\n\n"
                "Ghi tên đăng nhập vào ghi chú Job.",
                [("", "mailbox")],
            ),
            "mailbox": _step(
                "Tạo mailbox và mapping với tài khoản",
                f"{ADJUST}\n\n"
                "- Exchange hybrid: tạo remote mailbox cho tài khoản vừa tạo (`Enable-RemoteMailbox`), địa chỉ "
                "`ten.hodem@congty.com.vn`\n"
                "- Chờ Entra Connect đồng bộ (hoặc chạy delta sync), kiểm tra user xuất hiện trên M365\n"
                "- Thêm vào nhóm email phòng ban / distribution list\n"
                "- Tái tuyển: kích hoạt lại tài khoản và mailbox cũ thay vì tạo mới",
                [("", "license_band")],
            ),
            "license_band": _step(
                "Band từ B5 trở lên?",
                "App so sánh band trong request RTR. Quy tắc mẫu: band cao dùng gói đầy đủ.",
                [("Đúng", "license_full"), ("Sai", "license_role")],
                kind="check",
                check={"left": _fact("lifecycle.band"), "op": ">=", "right": _value("B5")},
            ),
            "license_full": _step(
                "Gán license M365 gói đầy đủ",
                f"{ADJUST}\n\nVí dụ: **Microsoft 365 E3** (+ add-on theo chức danh). Gán trên M365 admin center, "
                "bật MFA.",
                [("", "software")],
            ),
            "license_role": _step(
                "Chọn license M365 theo vị trí",
                f"{ADJUST}\n\n"
                "- Làm việc văn phòng (Outlook, Office desktop): **Business Standard / E3**\n"
                "- Chỉ cần email web và Teams: **F3 / Business Basic**",
                [("Văn phòng", "license_office"), ("Frontline", "license_frontline")],
            ),
            "license_office": _step(
                "Gán Business Standard / E3", "Gán license, bật MFA.", [("", "software")]
            ),
            "license_frontline": _step(
                "Gán F3 / Business Basic", "Gán license, bật MFA.", [("", "software")]
            ),
            "software": _step(
                "Đề xuất Office Tools theo chuẩn",
                f"{ADJUST}\n\n"
                "**Bộ chuẩn cho mọi nhân viên:** Microsoft 365 Apps (theo license), Teams, OneDrive, trình duyệt, "
                "PDF reader, 7-Zip, VPN client, phần mềm diệt virus.\n\n"
                "**Theo phòng ban:** ví dụ Kế toán – phần mềm kế toán; Giao dịch – Bloomberg / phần mềm giao dịch; "
                "IT – công cụ quản trị.\n\n"
                "Phần mềm ngoài danh mục: cần quản lý trực tiếp duyệt qua request SDP riêng.",
                [("", "devices")],
            ),
            "devices": _step(
                "Laptop theo tiêu chuẩn band đã đủ?",
                "So số laptop đang gán với Tiêu chuẩn cấp phát theo band (Cấu hình module).",
                [("Đúng", "biostar"), ("Sai", "stock")],
                kind="check",
                check={
                    "left": _fact("lifecycle.assets_in_use", "Laptop"),
                    "op": ">=",
                    "right": _fact("lifecycle.standard_quantity", "Laptop"),
                },
            ),
            "stock": _step(
                "Lấy thiết bị trong kho",
                "Kho thiết bị gợi ý tài sản In Store theo band (hàng thu hồi trước). Chọn tài sản trong Job onboard "
                "để Job gán cho nhân viên trên SDP.",
                [("Kho có đủ", "biostar"), ("Kho thiếu", "purchase")],
                [("lifecycle.stock", "Kho thiết bị"), ("lifecycle.jobs", "Chọn tài sản trong Job")],
            ),
            "purchase": _step(
                "Đề xuất mua phần còn thiếu",
                "Lập đề xuất mua (điền sẵn từ Kho thiết bị), kèm bằng chứng Function Head duyệt. Trong lúc chờ hàng, "
                "cấp máy tạm nếu có và ghi chú trên Job.",
                [("", "biostar")],
                [("workload.purchases", "Đề xuất mua thiết bị")],
            ),
            "biostar": _step(
                "Tạo user BioStar, đăng ký thẻ / vân tay / khuôn mặt",
                "Job onboard tạo user BioStar với nhóm quyền ra vào theo cấu hình; Operator đăng ký thẻ / vân tay / "
                "khuôn mặt tại máy và xác nhận bước trong Job.",
                [("", "handover_doc")],
                [("lifecycle.jobs", "Làm từng bước trong Job")],
            ),
            "handover_doc": _step(
                "In biên bản bàn giao",
                "Job lập **Biên bản Bàn giao** từ tài sản đã gán. Chốt số biên bản → in → nhân viên và IT ký → tải "
                "bản đã ký lên (Job đính kèm vào request SDP).",
                [("", "handover")],
                [("lifecycle.documents", "Biên bản"), ("lifecycle.jobs", "Mở Job onboard")],
            ),
            "handover": _step(
                "Bàn giao và hướng dẫn nhân viên",
                "- Đăng nhập lần đầu, đổi mật khẩu, đăng ký **MFA**\n"
                "- Kiểm tra Outlook, Teams, OneDrive, máy in, VPN\n"
                "- Gửi hướng dẫn sử dụng và kênh hỗ trợ IT",
                [("", "close")],
            ),
            "close": _step(
                "Đóng request SDP",
                "Bước cuối của Job: ghi chú kết quả, đính kèm biên bản và đóng request trên SDP.",
                [("", "done")],
                [("lifecycle.jobs", "Hoàn tất Job")],
            ),
            "done": _step(
                "Onboard hoàn tất theo tiêu chuẩn",
                "Tài khoản, mailbox, license, phần mềm, thiết bị, ra vào và biên bản đã xong.",
                kind="outcome",
                outcome={"result": "standard", "approval": "Không cần duyệt thêm"},
            ),
        },
    },
}

TEMPLATES: dict[str, dict[str, Any]] = {"onboard": ONBOARD}
