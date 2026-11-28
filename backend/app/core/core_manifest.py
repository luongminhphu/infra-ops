"""The platform core's own manifest: permissions, scheduled tasks, dashboard widgets, search, navigation.

Only the entry points (``app.main``, ``app.worker``, ``app.cli``) and tests import this module: anything inside
``app.core`` importing it would close an import cycle (guarded by ``tests/test_import_cycles.py``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import (
    NAV_STATS,
    DashboardWidget,
    ModuleManifest,
    NavItem,
    Permission,
    ScheduledTask,
)
from app.core.permissions import (
    AUDIT_VIEW,
    BRANDING_MANAGE,
    CHECKLIST_MANAGE,
    CONNECTIONS_MANAGE,
    CONNECTIONS_TEST,
    CONNECTIONS_VIEW,
    DASHBOARD_VIEW,
    NOTIFICATIONS_MANAGE,
    RBAC_MANAGE,
)
from app.core.search.sources import SEARCH_SOURCES


async def _connections_widget(session: AsyncSession) -> dict[str, Any]:
    from app.core.dashboard.widgets import connections_status

    return await connections_status(session)


async def _expiring_widget(session: AsyncSession) -> dict[str, Any]:
    from app.core.dashboard.widgets import expiring_secrets

    return await expiring_secrets(session)


async def _notifications_run(session: AsyncSession) -> dict[str, Any]:
    from app.core.notifications.service import run

    return await run(session)


async def _workflow_pump(session: AsyncSession) -> dict[str, Any]:
    from app.core.workflow.engine import pump

    return await pump(session)


CORE_MANIFEST = ModuleManifest(
    id="core",
    name="Hệ thống",
    description="Nền tảng dùng chung: kết nối, phân quyền, nhật ký.",
    permissions=[
        Permission(DASHBOARD_VIEW, "Xem trang tổng quan", ("manager", "supervisor", "operator", "viewer")),
        Permission(CONNECTIONS_VIEW, "Xem danh sách kết nối hệ thống", ("manager", "supervisor", "operator")),
        Permission(CONNECTIONS_MANAGE, "Thêm, sửa, xoá kết nối và nhập secret"),
        Permission(CONNECTIONS_TEST, "Kiểm tra kết nối", ("manager", "supervisor", "operator")),
        Permission(AUDIT_VIEW, "Xem nhật ký kiểm tra", ("manager",)),
        Permission(RBAC_MANAGE, "Quản lý người dùng và phân quyền"),
        Permission(BRANDING_MANAGE, "Đổi logo in trên báo cáo và biên bản"),
        Permission(NOTIFICATIONS_MANAGE, "Cấu hình thư tóm tắt “Việc tiếp theo” (giờ gửi, SMTP, Teams)"),
        Permission(
            CHECKLIST_MANAGE, "Sửa checklist và yêu cầu bằng chứng của các bước quy trình", ("manager",)
        ),
    ],
    scheduled_tasks=[
        ScheduledTask(
            "core.workflow",
            "Chạy các bước tự động của Job",
            _workflow_pump,
            interval_seconds=15,
            timeout_seconds=600,
            initial_delay_seconds=5,
        ),
        ScheduledTask(
            "core.notifications",
            "Gửi thư tóm tắt “Việc tiếp theo” theo giờ đã cấu hình",
            _notifications_run,
            interval_seconds=300,
            timeout_seconds=600,
            initial_delay_seconds=30,
        ),
    ],
    dashboard_widgets=[
        DashboardWidget(
            "core.connections",
            "Trạng thái kết nối",
            "connections",
            CONNECTIONS_MANAGE,  # connection health is the administrators' concern
            _connections_widget,
            order=10,
        ),
        DashboardWidget(
            "core.expiring_secrets",
            "Mật khẩu / chứng chỉ sắp hết hạn",
            "expiring_secrets",
            CONNECTIONS_MANAGE,
            _expiring_widget,
            order=20,
        ),
    ],
    search_sources=SEARCH_SOURCES,
    nav=[
        NavItem("Trang chủ", "/", "layout-dashboard", DASHBOARD_VIEW),
        NavItem("Việc của tôi", "/me/work", "user-check", DASHBOARD_VIEW),
        NavItem("Thông báo", "/me/notifications", "bell", DASHBOARD_VIEW),
        NavItem("Tổng quan số liệu", "/overview", "layout-dashboard", DASHBOARD_VIEW, NAV_STATS),
        NavItem("Kết nối hệ thống", "/settings/connections", "plug", CONNECTIONS_VIEW),
        NavItem("Nhật ký kiểm tra", "/settings/audit", "scroll-text", AUDIT_VIEW),
        NavItem("Người dùng & quyền", "/settings/access", "shield-check", RBAC_MANAGE),
        NavItem("Logo báo cáo", "/settings/branding", "image", BRANDING_MANAGE),
    ],
)
