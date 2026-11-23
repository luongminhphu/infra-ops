"""Module 2 – Helpdesk/Support workload from ServiceDesk Plus.

Syncs SDP requests per technician (Operator), lets Managers reassign them (written back to SDP)
and reports statistics by period.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import ModuleManifest, NavItem, Permission, ScheduledTask
from app.modules.workload.api import router
from app.modules.workload.workflows import REASSIGN


async def _sync(session: AsyncSession) -> dict[str, Any]:
    from app.modules.workload.sync import scheduled_sync

    return await scheduled_sync(session)


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
        Permission("workload.sync.run", "Đồng bộ task từ SDP ngay", ("manager", "supervisor", "operator")),
        Permission(
            "workload.settings.manage", "Cấu hình module và liên kết Operator ↔ technician", ("manager",)
        ),
    ],
    routers=[router],
    workflows=[REASSIGN],
    nav=[
        NavItem("Công việc", "/workload/tickets", "list-checks", "workload.tickets.view"),
        NavItem("Thống kê công việc", "/workload/stats", "chart-column", "workload.stats.view"),
        NavItem("Cấu hình công việc", "/workload/settings", "settings", "workload.settings.manage"),
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
