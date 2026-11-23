"""Permissions and navigation owned by the platform core."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import DashboardWidget, ModuleManifest, NavItem, Permission, ScheduledTask

DASHBOARD_VIEW = "core.dashboard.view"
CONNECTIONS_VIEW = "core.connections.view"
CONNECTIONS_MANAGE = "core.connections.manage"
CONNECTIONS_TEST = "core.connections.test"
AUDIT_VIEW = "core.audit.view"
RBAC_MANAGE = "core.rbac.manage"
CHECKLIST_MANAGE = "core.workflow.checklist.manage"


async def _connections_widget(session: AsyncSession) -> dict[str, Any]:
    from app.core.dashboard.widgets import connections_status

    return await connections_status(session)


async def _expiring_widget(session: AsyncSession) -> dict[str, Any]:
    from app.core.dashboard.widgets import expiring_secrets

    return await expiring_secrets(session)


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
    ],
    dashboard_widgets=[
        DashboardWidget(
            "core.connections",
            "Trạng thái kết nối",
            "connections",
            CONNECTIONS_VIEW,
            _connections_widget,
            order=10,
        ),
        DashboardWidget(
            "core.expiring_secrets",
            "Mật khẩu / chứng chỉ sắp hết hạn",
            "expiring_secrets",
            CONNECTIONS_VIEW,
            _expiring_widget,
            order=20,
        ),
    ],
    nav=[
        NavItem("Tổng quan", "/", "layout-dashboard", DASHBOARD_VIEW),
        NavItem("Việc của tôi", "/me/work", "user-check", DASHBOARD_VIEW),
        NavItem("Kết nối hệ thống", "/settings/connections", "plug", CONNECTIONS_VIEW),
        NavItem("Nhật ký kiểm tra", "/settings/audit", "scroll-text", AUDIT_VIEW),
        NavItem("Người dùng & quyền", "/settings/access", "shield-check", RBAC_MANAGE),
    ],
)
