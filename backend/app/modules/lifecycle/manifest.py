"""Module 1 – IT Lifecycle (onboard/offboard).

Phase 2: read SDP (RTR requests, users, assets) and BioStar, reconcile by employee ID.
Microsoft systems are deferred by the product owner.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import DashboardWidget, ModuleManifest, NavItem, Permission, ScheduledTask
from app.modules.lifecycle.api import router
from app.modules.lifecycle.assignment import REASSIGN_JOB
from app.modules.lifecycle.documents import HANDOVER, RETURN
from app.modules.lifecycle.documents_api import router as documents_router
from app.modules.lifecycle.inventory_api import router as inventory_router
from app.modules.lifecycle.jobs_api import router as jobs_router
from app.modules.lifecycle.workflows import OFFBOARD, ONBOARD


async def _sync(session: AsyncSession) -> dict[str, Any]:
    from app.modules.lifecycle.sync import scheduled_sync

    return await scheduled_sync(session)


async def _jobs(session: AsyncSession) -> dict[str, Any]:
    from sqlalchemy import func, select

    from app.core.workflow.models import Job

    rows = (
        await session.execute(
            select(Job.workflow_key, Job.status, func.count())
            .where(Job.module_id == "lifecycle")
            .group_by(Job.workflow_key, Job.status)
        )
    ).all()
    return {"counts": [{"workflow": w, "status": s, "count": n} for w, s, n in rows]}


async def _summary(session: AsyncSession) -> dict[str, Any]:
    from app.modules.lifecycle.widgets import reconciliation_summary

    return await reconciliation_summary(session)


manifest = ModuleManifest(
    id="lifecycle",
    name="Vòng đời nhân sự",
    description="Onboard/Offboard nhân sự và đối soát tài khoản, tài sản, ra vào.",
    permissions=[
        Permission(
            "lifecycle.job.view", "Xem Job onboard/offboard", ("manager", "supervisor", "operator", "viewer")
        ),
        Permission(
            "lifecycle.job.execute", "Thực thi Job onboard/offboard", ("manager", "supervisor", "operator")
        ),
        Permission(
            "lifecycle.job.assign",
            "Chuyển Job onboard/offboard cho Operator khác (đổi Technician trên SDP)",
            ("manager", "supervisor"),
        ),
        Permission("lifecycle.job.override_close", "Đóng Job khi chưa có biên bản đã ký", ("manager",)),
        Permission(
            "lifecycle.reconciliation.view", "Xem đối soát", ("manager", "supervisor", "operator", "viewer")
        ),
        Permission(
            "lifecycle.reconciliation.sync",
            "Đồng bộ dữ liệu và đánh dấu lệch đã biết",
            ("manager", "supervisor", "operator"),
        ),
        Permission(
            "lifecycle.documents.view",
            "Xem biên bản bàn giao/thu hồi",
            ("manager", "supervisor", "operator", "viewer"),
        ),
        Permission(
            "lifecycle.documents.create",
            "Lập, chốt biên bản và tải bản đã ký",
            ("manager", "supervisor", "operator"),
        ),
        Permission(
            "lifecycle.templates.manage", "Quản lý mẫu biên bản và huỷ biên bản đã chốt", ("manager",)
        ),
        Permission("lifecycle.settings.manage", "Cấu hình mapping của module", ("manager",)),
    ],
    routers=[router, documents_router, jobs_router, inventory_router],
    workflows=[ONBOARD, OFFBOARD, REASSIGN_JOB],
    document_kinds=[HANDOVER, RETURN],
    nav=[
        NavItem(
            "Đối soát theo Mã NV",
            "/lifecycle/reconciliation",
            "git-compare-arrows",
            "lifecycle.reconciliation.view",
        ),
        NavItem("Job onboard / offboard", "/lifecycle/jobs", "workflow", "lifecycle.job.view"),
        NavItem("Lưu đồ quy trình", "/lifecycle/flows", "route", "lifecycle.job.view"),
        NavItem("Kho & cấp phát", "/lifecycle/inventory", "package", "lifecycle.job.view"),
        NavItem("Request RTR", "/lifecycle/requests", "ticket", "lifecycle.reconciliation.view"),
        NavItem("Biên bản", "/lifecycle/documents", "file-text", "lifecycle.documents.view"),
        NavItem("Đồng bộ dữ liệu", "/lifecycle/sync", "refresh-cw", "lifecycle.reconciliation.view"),
        NavItem("Mẫu biên bản", "/lifecycle/templates", "file-cog", "lifecycle.templates.manage"),
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
            "lifecycle.jobs", "Job onboard / offboard", "lifecycle_jobs", "lifecycle.job.view", _jobs, order=2
        ),
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
