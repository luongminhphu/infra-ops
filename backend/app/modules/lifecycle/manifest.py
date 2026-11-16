"""Module 1 – IT Lifecycle (onboard/offboard).

Phase 1 only registers permissions and connector usage; routes, models and
workflows arrive in Phase 2+.
"""

from __future__ import annotations

from app.core.modules.manifest import ModuleManifest, Permission

manifest = ModuleManifest(
    id="lifecycle",
    name="Vòng đời nhân sự",
    description="Onboard/Offboard nhân sự và đối soát tài khoản, tài sản, ra vào.",
    permissions=[
        Permission("lifecycle.job.view", "Xem Job onboard/offboard", ("helpdesk", "viewer")),
        Permission("lifecycle.job.execute", "Thực thi Job onboard/offboard", ("helpdesk",)),
        Permission("lifecycle.job.override_close", "Đóng Job khi chưa có biên bản đã ký"),
        Permission("lifecycle.reconciliation.view", "Xem đối soát", ("helpdesk", "viewer")),
        Permission("lifecycle.reconciliation.sync", "Đồng bộ dữ liệu đối soát thủ công", ("helpdesk",)),
        Permission("lifecycle.templates.manage", "Quản lý mẫu biên bản"),
        Permission("lifecycle.settings.manage", "Cấu hình mapping của module"),
    ],
    # Microsoft connectors (entra, ad, exchange) are added once that integration is cleared.
    connectors_used=["sdp", "biostar"],
)
