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
                "Bắt buộc: **Mã NV**, họ tên, phòng ban, **chức danh**, ngày hiệu lực, quản lý trực tiếp, "
                "địa điểm làm việc.\n\nTrang Request RTR đánh dấu dòng thiếu/sai thông tin.",
                [("Đủ", "reconcile"), ("Thiếu / sai", "ask_hr")],
                [("lifecycle.requests", "Kiểm tra lỗi đọc request")],
            ),
            "ask_hr": _step(
                "Yêu cầu HR bổ sung",
                "Ghi chú trên request SDP các trường còn thiếu, gán lại cho HR (người tạo RTR). Không tự đoán Mã NV "
                "hay chức danh.\n\nKhi HR cập nhật, đồng bộ lại rồi kiểm tra lần nữa.",
                [("Đã bổ sung", "complete")],
            ),
            "reconcile": _step(
                "Đối soát thông tin nhân viên",
                f"{ADJUST}\n\n"
                "Quy tắc: **mỗi lần onboard là một Mã NV mới và tài khoản mới** – kể cả người từng làm ở công ty, "
                "không dùng lại tài khoản, mailbox hay thiết bị cũ.\n\n"
                "Tìm Mã NV trên trang Đối soát để chắc Mã NV mới chưa có ở hệ thống nào (chưa có user SDP / BioStar là "
                "bình thường). Họ tên, phòng ban khác với request: xác nhận với HR.",
                [("", "ad_account")],
                [("lifecycle.reconciliation", "Đối soát theo Mã NV")],
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
                "- Thêm vào nhóm email phòng ban / distribution list",
                [("", "license_level")],
            ),
            "license_level": _step(
                "Cấp bậc theo chức danh?",
                f"{ADJUST}\n\n"
                "Cấp bậc lấy từ **chức danh** trong request RTR theo Ma trận cấp phát (Cấu hình module); trang Kho thiết "
                "bị ghi cấp bậc của từng nhân viên mới. Quy tắc mẫu: cấp quản lý trở lên dùng gói license đầy đủ.",
                [("Quản lý / lãnh đạo", "license_full"), ("Nhân viên / Intern / Vendor", "license_role")],
                [("lifecycle.stock", "Xem cấp bậc ở Kho thiết bị")],
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
                [("", "stock")],
            ),
            "stock": _step(
                "Lấy thiết bị trong kho",
                "Mã NV mới chưa có thiết bị: cấp đủ theo Ma trận cấp phát (cấp bậc theo chức danh). Kho thiết bị gợi ý tài sản "
                "In Store (hàng thu hồi trước). Chọn tài sản trong Job onboard để Job gán cho nhân viên trên SDP.",
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

OFFBOARD: dict[str, Any] = {
    "title": "Offboard nhân viên nghỉ việc",
    "category": "Vòng đời nhân sự",
    "description": "Từ request nghỉ việc trên SDP đến biên bản thu hồi: khoá ra vào, tài khoản AD, mailbox, license, "
    "thu hồi thiết bị về kho và rà soát ISO cuối tháng.",
    "tree": {
        "root": "receive",
        "nodes": {
            "receive": _step(
                "Tiếp nhận request nghỉ việc trên SDP",
                "Request nghỉ việc từ RTR được đồng bộ về infra-ops và tạo **Job offboard nháp**.\n\n"
                "- Nhận Job trên trang chính (hàng đợi team) hoặc trong Job\n"
                "- Đọc **ngày nghỉ (ngày hiệu lực)**: khoá ra vào và tài khoản đúng ngày này, không sớm hơn",
                [("", "complete")],
                [("lifecycle.requests", "Xem request RTR"), ("lifecycle.jobs", "Mở Job offboard")],
            ),
            "complete": _step(
                "Request có đủ thông tin?",
                "Bắt buộc: **Mã NV**, họ tên, phòng ban, ngày nghỉ, quản lý trực tiếp (người nhận bàn giao dữ liệu).",
                [("Đủ", "reconcile"), ("Thiếu / sai", "ask_hr")],
                [("lifecycle.requests", "Kiểm tra lỗi đọc request")],
            ),
            "ask_hr": _step(
                "Yêu cầu HR bổ sung",
                "Ghi chú trên request SDP các trường còn thiếu, gán lại cho HR. Không tự đoán Mã NV hay ngày nghỉ.",
                [("Đã bổ sung", "complete")],
            ),
            "reconcile": _step(
                "Đối soát tài khoản và tài sản theo Mã NV",
                "Trang Đối soát cho biết nhân viên đang có: user BioStar, user SDP, **tài sản đang gán**.\n\n"
                "Họ tên hoặc phòng ban khác giữa các hệ thống: xác nhận với HR trước khi khoá.",
                [("", "biostar")],
                [("lifecycle.reconciliation", "Đối soát theo Mã NV")],
            ),
            "biostar": _step(
                "Khoá ra vào BioStar",
                "Bước đầu của Job offboard: vô hiệu hoá / xoá user BioStar theo cấu hình module, nhập lại Mã NV để "
                "xác nhận. Khoá ra vào **không chờ** thu hồi thiết bị.",
                [("", "ad_disable")],
                [("lifecycle.jobs", "Làm bước trong Job")],
            ),
            "ad_disable": _step(
                "Vô hiệu hoá tài khoản AD",
                f"{ADJUST}\n\n"
                "Làm thủ công trên AD (tích hợp Microsoft đang tạm hoãn), cuối ngày nghỉ:\n\n"
                "1. Disable tài khoản, đặt lại mật khẩu ngẫu nhiên\n"
                "2. Ghi ngày nghỉ và số request vào Description\n"
                "3. Chuyển sang OU *Disabled Users*, gỡ khỏi nhóm bảo mật / thư mục share / nhóm email\n"
                "4. Thu hồi phiên đăng nhập M365 (*Revoke sessions*), gỡ thiết bị MFA",
                [("", "mailbox")],
            ),
            "mailbox": _step(
                "Xử lý mailbox và dữ liệu",
                f"{ADJUST}\n\n"
                "- Chuyển mailbox thành **shared mailbox** cho quản lý trực tiếp (hoặc forward theo yêu cầu), giữ N ngày\n"
                "- OneDrive: cấp quyền cho quản lý trực tiếp lấy dữ liệu trước khi xoá\n"
                "- Ẩn khỏi danh bạ (GAL)",
                [("", "license")],
            ),
            "license": _step(
                "Thu hồi license M365",
                f"{ADJUST}\n\nSau khi mailbox đã chuyển thành shared mailbox: gỡ license để dùng lại cho nhân viên mới.",
                [("", "has_laptop")],
            ),
            "has_laptop": _step(
                "Nhân viên đang giữ laptop?",
                "App tự kiểm tra từ lần đối soát gần nhất.",
                [("Đúng", "collect"), ("Sai", "recheck_assets")],
                kind="check",
                check={"left": _fact("lifecycle.assets_in_use", "Laptop"), "op": ">=", "right": _value("1")},
            ),
            "recheck_assets": _step(
                "Đối chiếu lại tài sản",
                "Không thấy laptop đang gán trên SDP. Vẫn kiểm tra: màn hình, dock, phụ kiện, thiết bị mượn chưa ghi "
                "SDP, thẻ ra vào. Không có gì cần thu hồi thì ghi rõ trên Job.",
                [("", "collect")],
                [("lifecycle.reconciliation", "Tài sản theo Mã NV")],
            ),
            "collect": _step(
                "Thu hồi thiết bị và ghi tình trạng",
                "Bước *Thu hồi tài sản* trong Job: nhận lại từng thiết bị theo danh sách, chọn tình trạng **Tốt / Hỏng "
                "/ Thiếu phụ kiện / Chưa thu hồi**, chụp ảnh làm bằng chứng.",
                [("Thu đủ, tình trạng tốt", "to_store"), ("Thiếu / hỏng / chưa trả", "missing")],
                [("lifecycle.jobs", "Làm bước trong Job")],
            ),
            "missing": _step(
                "Thiết bị thiếu, hỏng hoặc chưa trả",
                f"{ADJUST}\n\n"
                "Ghi rõ trên biên bản, báo quản lý trực tiếp và HR để xử lý theo quy định (bồi hoàn / trừ khi quyết "
                "toán). Thiết bị chưa trả: hẹn ngày, Job giữ ở trạng thái chờ.",
                [("", "to_store")],
            ),
            "to_store": _step(
                "Chuyển tài sản về kho",
                "Job bỏ gán user và chuyển tài sản đã nhận về **In Store** trên SDP. Sao lưu dữ liệu cần giữ rồi mới "
                "xoá trắng máy trước khi cấp lại.",
                [("", "return_doc")],
                [("lifecycle.jobs", "Làm bước trong Job"), ("lifecycle.stock", "Kho thiết bị")],
            ),
            "return_doc": _step(
                "Biên bản thu hồi",
                "Job lập **Biên bản Thu hồi** từ kết quả thu hồi. Chốt số → in → nhân viên và IT ký → tải bản đã ký "
                "lên (Job đính kèm vào request SDP).",
                [("", "close")],
                [("lifecycle.documents", "Biên bản"), ("lifecycle.jobs", "Mở Job offboard")],
            ),
            "close": _step(
                "Đóng request SDP",
                "Bước cuối của Job: ghi chú kết quả, đính kèm biên bản và đóng request trên SDP.",
                [("", "review")],
                [("lifecycle.jobs", "Hoàn tất Job")],
            ),
            "review": _step(
                "Rà soát nghỉ việc (ISO) cuối tháng",
                "Cuối tháng xuất danh sách nhân sự nghỉ việc kèm trạng thái khoá tài khoản / ra vào / thu hồi, gửi HR "
                "xác nhận (ISO 27001).",
                [("", "done")],
                [("lifecycle.offboard-review", "Rà soát nghỉ việc")],
            ),
            "done": _step(
                "Offboard hoàn tất theo tiêu chuẩn",
                "Ra vào, tài khoản, mailbox, license đã xử lý; thiết bị đã về kho và có biên bản thu hồi.",
                kind="outcome",
                outcome={"result": "standard", "approval": "Không cần duyệt thêm"},
            ),
        },
    },
}

EXTRA_DEVICE: dict[str, Any] = {
    "title": "Cấp phát bổ sung thiết bị",
    "category": "Thiết bị",
    "description": "Nhân viên xin thêm thiết bị (màn hình, phụ kiện…): so với ma trận cấp phát theo chức danh, xin duyệt khi vượt, lấy "
    "từ kho hoặc đề xuất mua, gán trên SDP và lập biên bản bàn giao.",
    "tree": {
        "root": "receive",
        "nodes": {
            "receive": _step(
                "Tiếp nhận request cấp thêm thiết bị",
                "Request trên SDP (vd. *cấp thêm màn hình*) hiện ở trang Công việc. Nhận request, đọc rõ: thiết bị gì, "
                "số lượng, lý do, người dùng (Mã NV).",
                [("", "what")],
                [("workload.tickets", "Công việc")],
            ),
            "what": _step(
                "Loại thiết bị?",
                "Chọn đúng nhánh để áp tiêu chuẩn cấp phát.",
                [
                    ("Màn hình", "monitor_std"),
                    ("Phụ kiện (chuột, bàn phím, tai nghe…)", "accessory"),
                    ("Thiết bị khác", "approval"),
                ],
            ),
            "monitor_std": _step(
                "Số màn hình đang dùng còn dưới tiêu chuẩn cấp phát?",
                "App so số màn hình đang gán (đối soát gần nhất) với Ma trận cấp phát – cấp bậc theo chức danh trong RTR.",
                [("Đúng", "stock"), ("Sai", "approval")],
                kind="check",
                check={
                    "left": _fact("lifecycle.assets_in_use", "Monitor"),
                    "op": "<",
                    "right": _fact("lifecycle.standard_quantity", "Monitor"),
                },
            ),
            "accessory": _step(
                "Phụ kiện tiêu hao",
                f"{ADJUST}\n\n"
                "Phụ kiện giá trị thấp cấp thẳng từ kho, ghi trên request (không cần biên bản). Phụ kiện có quản lý "
                "trên SDP (dock, tai nghe họp…) đi tiếp như thiết bị.",
                [("Có quản lý trên SDP", "stock"), ("Cấp thẳng", "close")],
            ),
            "approval": _step(
                "Ngoài tiêu chuẩn: cần Function Head duyệt",
                "Đã đủ tiêu chuẩn cấp phát hoặc thiết bị ngoài danh mục: đề nghị người dùng xin duyệt của **Function "
                "Head** (email / phê duyệt trên SDP), đính kèm vào request.",
                [("Đã duyệt", "stock"), ("Không duyệt", "rejected")],
            ),
            "rejected": _step(
                "Không cấp",
                "Ghi lý do trên request SDP và đóng request.",
                kind="outcome",
                outcome={"result": "exception", "approval": "Function Head không duyệt"},
            ),
            "stock": _step(
                "Lấy thiết bị trong kho",
                "Kho thiết bị liệt kê tài sản In Store (ưu tiên hàng thu hồi còn tốt).",
                [("Kho có", "assign"), ("Kho hết", "purchase")],
                [("lifecycle.stock", "Kho thiết bị")],
            ),
            "purchase": _step(
                "Đề xuất mua",
                "Lập đề xuất mua từ request (kèm bằng chứng duyệt nếu ngoài tiêu chuẩn). Hàng về thì nhận hàng – "
                "tài sản vào kho In Store – rồi cấp.",
                [("Hàng đã về", "assign")],
                [("workload.purchases", "Đề xuất mua thiết bị")],
            ),
            "assign": _step(
                "Gán tài sản cho nhân viên trên SDP",
                "Trên SDP: gán user (đúng Mã NV), trạng thái **In Use**, phòng ban. Kiểm tra serial / mã tài sản khớp "
                "thiết bị đưa tận tay.",
                [("", "handover_doc")],
            ),
            "handover_doc": _step(
                "Lập biên bản bàn giao",
                "Biên bản → tạo mới loại **Bàn giao**, chọn nhân viên và tài sản vừa gán. Chốt số → in → ký → tải bản "
                "đã ký lên.",
                [("", "close")],
                [("lifecycle.documents", "Biên bản")],
            ),
            "close": _step(
                "Đóng request SDP",
                "Ghi chú tài sản đã cấp (hoặc phụ kiện đã đưa), đính kèm biên bản nếu có, đóng request.",
                [("", "done")],
                [("workload.tickets", "Công việc")],
            ),
            "done": _step(
                "Cấp phát hoàn tất",
                "Thiết bị đã gán đúng người trên SDP và có biên bản bàn giao.",
                kind="outcome",
                outcome={"result": "standard", "approval": "Ngoài tiêu chuẩn: kèm duyệt của Function Head"},
            ),
        },
    },
}

RECALL_DEVICE: dict[str, Any] = {
    "title": "Thu hồi thiết bị",
    "category": "Thiết bị",
    "description": "Thu hồi thiết bị khi thay thế, nâng cấp, hết hạn mượn hoặc dư tiêu chuẩn: sao lưu dữ liệu, kiểm "
    "tra tình trạng, về kho trên SDP và lập biên bản thu hồi. Nghỉ việc dùng Playbook Offboard.",
    "tree": {
        "root": "receive",
        "nodes": {
            "receive": _step(
                "Tiếp nhận yêu cầu thu hồi",
                "Từ request SDP (thay máy, nâng cấp, trả thiết bị mượn) hoặc từ rà soát dư tiêu chuẩn. Xác định Mã NV, "
                "thiết bị (mã tài sản / serial) và lý do.",
                [("", "reason")],
                [("workload.tickets", "Công việc")],
            ),
            "reason": _step(
                "Lý do thu hồi?",
                "Nghỉ việc đi theo Job offboard (đã gồm thu hồi và biên bản).",
                [
                    ("Nghỉ việc", "offboard"),
                    ("Thay thế / nâng cấp", "backup"),
                    ("Hết hạn mượn / dư tiêu chuẩn", "over_std"),
                ],
            ),
            "offboard": _step(
                "Làm theo Job offboard",
                "Thu hồi khi nghỉ việc nằm trong Job offboard – mở Playbook *Offboard nhân viên nghỉ việc*.",
                [("", "done_offboard")],
                [("lifecycle.jobs", "Job onboard / offboard")],
            ),
            "done_offboard": _step(
                "Chuyển sang quy trình offboard",
                "Không thu hồi riêng lẻ để tránh hai biên bản cho cùng thiết bị.",
                kind="outcome",
                outcome={"result": "standard", "approval": "Theo Job offboard"},
            ),
            "over_std": _step(
                "Số laptop đang dùng vượt tiêu chuẩn cấp phát?",
                "App so số laptop đang gán với Ma trận cấp phát (cấp bậc theo chức danh). Đổi loại tài sản trong điều kiện nếu "
                "thu hồi màn hình / thiết bị khác.",
                [("Đúng", "schedule"), ("Sai", "confirm_mgr")],
                kind="check",
                check={
                    "left": _fact("lifecycle.assets_in_use", "Laptop"),
                    "op": ">",
                    "right": _fact("lifecycle.standard_quantity", "Laptop"),
                },
            ),
            "confirm_mgr": _step(
                "Xác nhận với quản lý trực tiếp",
                "Không vượt tiêu chuẩn: xác nhận lý do thu hồi (hết hạn mượn, đổi vị trí…) với quản lý trực tiếp trước "
                "khi hẹn nhận lại.",
                [("Thu hồi", "schedule"), ("Giữ lại", "keep")],
            ),
            "keep": _step(
                "Giữ nguyên thiết bị",
                "Ghi lý do trên request và đóng.",
                kind="outcome",
                outcome={"result": "adhoc", "approval": "Quản lý trực tiếp xác nhận giữ lại"},
            ),
            "backup": _step(
                "Sao lưu dữ liệu người dùng",
                f"{ADJUST}\n\n"
                "- Đồng bộ OneDrive, kiểm tra không còn dữ liệu chỉ nằm trên ổ máy\n"
                "- Thay máy: chuyển dữ liệu và cài phần mềm trên máy mới trước, người dùng xác nhận rồi mới thu máy cũ\n"
                "- Không xoá trắng máy cũ trước khi người dùng xác nhận đủ dữ liệu",
                [("", "schedule")],
            ),
            "schedule": _step(
                "Hẹn nhận lại thiết bị",
                "Hẹn ngày giờ, nhắc mang đủ phụ kiện (sạc, dock, túi). Ghi lịch hẹn trên request.",
                [("", "collect")],
            ),
            "collect": _step(
                "Nhận lại và kiểm tra tình trạng",
                "Đối chiếu mã tài sản / serial với SDP, kiểm tra ngoại quan, bật máy, phụ kiện đi kèm. Chụp ảnh làm "
                "bằng chứng.",
                [("Tốt", "to_store"), ("Hỏng", "broken"), ("Thiếu phụ kiện", "missing")],
            ),
            "broken": _step(
                "Thiết bị hỏng",
                "Ghi tình trạng trên biên bản. Trong Kho thiết bị: đưa vào chờ sửa / bảo hành, hoặc đề nghị thanh lý "
                "nếu không sửa được.",
                [("", "return_doc")],
                [("lifecycle.stock", "Kho thiết bị")],
            ),
            "missing": _step(
                "Thiếu phụ kiện",
                f"{ADJUST}\n\nGhi rõ phụ kiện thiếu trên biên bản, báo quản lý trực tiếp để xử lý theo quy định.",
                [("", "to_store")],
            ),
            "to_store": _step(
                "Về kho: bỏ gán user, In Store trên SDP",
                f"{ADJUST}\n\n"
                "Trên SDP: bỏ gán user, trạng thái **In Store**. Xoá trắng / cài lại máy theo quy định trước khi cấp "
                "cho người khác.",
                [("", "return_doc")],
                [("lifecycle.stock", "Kho thiết bị")],
            ),
            "return_doc": _step(
                "Lập biên bản thu hồi",
                "Biên bản → tạo mới loại **Thu hồi**, chọn nhân viên và tài sản, ghi tình trạng. Chốt số → in → ký → "
                "tải bản đã ký lên.",
                [("", "close")],
                [("lifecycle.documents", "Biên bản")],
            ),
            "close": _step(
                "Đóng request SDP",
                "Ghi chú kết quả, đính kèm biên bản thu hồi, đóng request.",
                [("", "done")],
                [("workload.tickets", "Công việc")],
            ),
            "done": _step(
                "Thu hồi hoàn tất",
                "Thiết bị đã về kho (hoặc chờ sửa / thanh lý) và có biên bản thu hồi.",
                kind="outcome",
                outcome={"result": "standard", "approval": "Không cần duyệt thêm"},
            ),
        },
    },
}

# Steps the app's Job runs are "Bước của Job": the reader sees the Job step and its current checklist (Lưu đồ quy
# trình), so the template never repeats – and never drifts from – what the Job checks.
# node: (workflow step, note kept – only what the Job itself does not say)
JOB_STEPS = {
    "onboard": ("lifecycle.onboard", {
        "biostar": ("biostar_enroll", "Hẹn nhân viên đăng ký trực tiếp tại máy BioStar trong ngày đầu làm việc."),
        "handover_doc": ("handover_doc", ""),
        "close": ("close_request", ""),
    }),
    "offboard": ("lifecycle.offboard", {
        "biostar": ("biostar_offboard", "Khoá đúng ngày nghỉ – **không chờ** thu hồi thiết bị."),
        "collect": ("collect_assets", "Thiếu, hỏng hoặc chưa trả: đi nhánh bên dưới để báo quản lý trực tiếp và HR."),
        "to_store": ("return_assets", "Sao lưu dữ liệu cần giữ rồi mới xoá trắng máy trước khi cấp lại."),
        "return_doc": ("return_doc", ""),
        "close": ("close_request", ""),
    }),
}  # fmt: skip
for _tpl, (_wf, _steps) in ((ONBOARD, JOB_STEPS["onboard"]), (OFFBOARD, JOB_STEPS["offboard"])):
    for _node, (_step_key, _note) in _steps.items():
        n = _tpl["tree"]["nodes"][_node]
        n |= {
            "kind": "job",
            "job": {"workflow": _wf, "step": _step_key},
            "note": _note,
            # The step itself links to the Job; other tools (documents, stock, review) stay.
            "actions": [a for a in n["actions"] if a["tool"] != "lifecycle.jobs"],
        }

TEMPLATES: dict[str, dict[str, Any]] = {
    "onboard": ONBOARD,
    "offboard": OFFBOARD,
    "extra_device": EXTRA_DEVICE,
    "recall_device": RECALL_DEVICE,
}
