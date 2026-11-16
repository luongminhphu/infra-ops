"""Module 1 – IT Lifecycle (onboard/offboard).

Phase 2: read SDP (RTR requests, users, assets) and BioStar, reconcile by employee ID.
Microsoft systems are deferred by the product owner.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import DashboardWidget, ModuleManifest, NavItem, Permission, ScheduledTask
from app.modules.lifecycle.api import router


async def _sync(session: AsyncSession) -> dict[str, Any]:
    from app.modules.lifecycle.sync import scheduled_sync

    return await scheduled_sync(session)


async def _summary(session: AsyncSession) -> dict[str, Any]:
    from app.modules.lifecycle.widgets import reconciliation_summary

    return await reconciliation_summary(session)


manifest = ModuleManifest(
    id="lifecycle",
    name="Vòng đời nhân sự",
    description="Onboard/Offboard nhân sự và đối soát tài khoản, tài sản, ra vào.",
    permissions=[
        Permission("lifecycle.job.view", "Xem Job onboard/offboard", ("helpdesk", "viewer")),
        Permission("lifecycle.job.execute", "Thực thi Job onboard/offboard", ("helpdesk",)),
        Permission("lifecycle.job.override_close", "Đóng Job khi chưa có biên bản đã ký"),
        Permission("lifecycle.reconciliation.view", "Xem đối soát", ("helpdesk", "viewer")),
        Permission(
            "lifecycle.reconciliation.sync", "Đồng bộ dữ liệu và đánh dấu lệch đã biết", ("helpdesk",)
        ),
        Permission("lifecycle.templates.manage", "Quản lý mẫu biên bản"),
        Permission("lifecycle.settings.manage", "Cấu hình mapping của module"),
    ],
    routers=[router],
    nav=[
        NavItem(
            "Đối soát theo Mã NV",
            "/lifecycle/reconciliation",
            "git-compare-arrows",
            "lifecycle.reconciliation.view",
        ),
        NavItem("Request RTR", "/lifecycle/requests", "ticket", "lifecycle.reconciliation.view"),
        NavItem("Đồng bộ dữ liệu", "/lifecycle/sync", "refresh-cw", "lifecycle.reconciliation.view"),
        NavItem("Cấu hình module", "/lifecycle/settings", "settings", "lifecycle.settings.manage"),
    ],
    # Microsoft connectors (entra, ad, exchange) are added once that integration is cleared.
    connectors_used=["sdp", "biostar"],
    scheduled_tasks=[
        ScheduledTask(
            "lifecycle.sync",
            "Đọc SDP + BioStar và đối soát theo Mã NV",
            _sync,
            interval_seconds=1800,
            timeout_seconds=1800,
        ),
    ],
    dashboard_widgets=[
        DashboardWidget(
            "lifecycle.reconciliation",
            "Đối soát theo Mã NV",
            "lifecycle_reconciliation",
            "lifecycle.reconciliation.view",
            _summary,
            order=5,
        ),
    ],
    migrations_branch="lifecycle",
)
