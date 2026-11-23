"""Module 2 – Helpdesk/Support workload from ServiceDesk Plus.

Syncs SDP requests per technician (Operator), lets Managers reassign them (written back to SDP)
and reports statistics by period.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import DashboardWidget, ModuleManifest, NavItem, Permission, ScheduledTask
from app.modules.workload.api import router
from app.modules.workload.purchases_api import router as purchases_router
from app.modules.workload.workflows import REASSIGN


async def _sync(session: AsyncSession) -> dict[str, Any]:
    from app.modules.workload.sync import scheduled_sync

    return await scheduled_sync(session)


async def _empty(_: AsyncSession) -> dict[str, Any]:
    return {}


manifest = ModuleManifest(
    id="workload",
    name="Công việc Helpdesk",
    description="Task Helpdesk/Support trên ServiceDesk Plus theo từng Operator, chuyển việc và thống kê.",
    permissions=[
        Permission(
            "workload.tickets.view",
            "Xem task Helpdesk/Support",
            ("manager", "supervisor", "operator", "viewer"),
        ),
        Permission(
            "workload.tickets.assign",
            "Giao và chuyển task giữa các Operator (ghi sang SDP)",
            ("manager", "supervisor"),
        ),
        Permission(
            "workload.tickets.view_all",
            "Xem task và thống kê của mọi Operator (không có: chỉ thấy task của mình)",
            ("manager", "supervisor", "viewer"),
        ),
        Permission(
            "workload.stats.view", "Xem thống kê task", ("manager", "supervisor", "operator", "viewer")
        ),
        Permission(
            "workload.purchase.create",
            "Lập đề xuất mua thiết bị từ request",
            ("manager", "supervisor", "operator"),
        ),
        Permission(
            "workload.purchase.manage", "Duyệt và cập nhật trạng thái đề xuất mua", ("manager", "supervisor")
        ),
        Permission(
            "workload.catalog.manage",
            "Cập nhật bảng giá thiết bị (sau khi đối tác báo giá)",
            ("manager", "supervisor"),
        ),
        Permission("workload.po.close", "Chốt PO tổng và tạo email nháp", ("manager", "supervisor")),
        Permission("workload.sync.run", "Đồng bộ task từ SDP ngay", ("manager", "supervisor", "operator")),
        Permission(
            "workload.settings.manage", "Cấu hình module và liên kết Operator ↔ technician", ("manager",)
        ),
    ],
    routers=[router, purchases_router],
    workflows=[REASSIGN],
    nav=[
        NavItem("Công việc", "/workload/tickets", "list-checks", "workload.tickets.view"),
        NavItem("Thống kê công việc", "/workload/stats", "chart-column", "workload.stats.view"),
        NavItem("Đề xuất mua thiết bị", "/workload/purchases", "shopping-cart", "workload.tickets.view"),
        NavItem("Cấu hình công việc", "/workload/settings", "settings", "workload.settings.manage"),
    ],
    # The widget renders per-user data itself (operators see only their own tasks).
    dashboard_widgets=[
        DashboardWidget(
            "workload.next", "Công việc Helpdesk", "workload_next", "workload.tickets.view", _empty, order=1
        ),
    ],
    connectors_used=["sdp"],
    scheduled_tasks=[
        ScheduledTask(
            "workload.sync",
            "Đọc task Helpdesk/Support từ SDP",
            _sync,
            interval_seconds=600,
            timeout_seconds=1800,
        ),
    ],
    migrations_branch="workload",
)
