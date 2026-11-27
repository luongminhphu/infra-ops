"""Module 1 – IT Lifecycle (onboard/offboard).

Phase 2: read SDP (RTR requests, users, assets) and BioStar, reconcile by employee ID.
Microsoft systems are deferred by the product owner.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import (
    NAV_ASSETS,
    NAV_REVIEWS,
    NAV_SETUP,
    NAV_SOURCES,
    NAV_WORK,
    DashboardWidget,
    ModuleManifest,
    NavItem,
    NextActionSource,
    Permission,
    ScheduledTask,
)
from app.modules.lifecycle import next_actions
from app.modules.lifecycle.api import router
from app.modules.lifecycle.assignment import REASSIGN_JOB
from app.modules.lifecycle.attendance_api import router as attendance_router
from app.modules.lifecycle.documents import HANDOVER, RETURN
from app.modules.lifecycle.documents_api import router as documents_router
from app.modules.lifecycle.facts import FACTS
from app.modules.lifecycle.inventory_api import router as inventory_router
from app.modules.lifecycle.jobs_api import router as jobs_router
from app.modules.lifecycle.offboard_review import next_action as offboard_review_action
from app.modules.lifecycle.offboard_review_api import router as offboard_review_router
from app.modules.lifecycle.stock import ASSET_DISPOSE, ASSET_STATE
from app.modules.lifecycle.stock import router as stock_router
from app.modules.lifecycle.stocktake_api import next_action as stocktake_action
from app.modules.lifecycle.stocktake_api import router as stocktake_router
from app.modules.lifecycle.stocktake_record import STOCKTAKE_RECORD
from app.modules.lifecycle.workflows import OFFBOARD, ONBOARD, TRANSFER


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


async def _empty(session: AsyncSession) -> dict[str, Any]:
    return {}


async def _summary(session: AsyncSession) -> dict[str, Any]:
    from app.modules.lifecycle.widgets import reconciliation_summary

    return await reconciliation_summary(session)


RECON_VIEW = "lifecycle.reconciliation.view"

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
            "lifecycle.job.view_all",
            "Xem Job onboard/offboard của mọi người (không có: chỉ Job của mình và Job chưa ai nhận)",
            ("manager", "supervisor", "viewer"),
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
        Permission(
            "lifecycle.stock.manage",
            "Cập nhật tồn kho: đánh dấu hàng thu hồi, chuyển sang / trả về từ Chờ thanh lý trên SDP",
            ("manager", "supervisor"),
        ),
        Permission(
            "lifecycle.attendance.export",
            "Xuất báo cáo chấm công (log ra vào BioStar) theo Mã NV",
            ("manager", "supervisor", "operator"),
        ),
        Permission(
            "lifecycle.offboard_review.export",
            "Rà soát nhân sự nghỉ việc hằng tháng và lập email gửi HR (ISO 27001)",
            ("manager", "supervisor"),
        ),
        Permission(
            "lifecycle.stocktake.view",
            "Xem kỳ kiểm kê tài sản IT và xuất file đối chiếu cho Hành chánh / Kế toán",
            ("manager", "supervisor", "operator", "viewer"),
        ),
        Permission(
            "lifecycle.stocktake.view_all",
            "Xem toàn bộ kỳ kiểm kê (tiến độ mọi khu, chênh lệch, danh mục khu) và tải file Excel",
            ("manager", "supervisor", "viewer"),
        ),
        Permission(
            "lifecycle.stocktake.count",
            "Kiểm thực tế: đánh dấu tài sản có / không, thêm tài sản ngoài sổ",
            ("manager", "supervisor", "operator"),
        ),
        Permission(
            "lifecycle.stocktake.assign",
            "Giao khu kiểm kê cho Operator và chuyển tài sản giữa các khu",
            ("manager",),
        ),
        Permission(
            "lifecycle.stocktake.manage",
            "Mở / kết thúc / đóng kỳ kiểm kê và chọn hướng xử lý chênh lệch",
            ("manager", "supervisor"),
        ),
        # Administrator has every permission; only Managers among the other roles may dispose.
        Permission(
            "lifecycle.stock.dispose", "Thanh lý tài sản (trạng thái Disposed) trên SDP", ("manager",)
        ),
    ],
    routers=[
        router,
        documents_router,
        jobs_router,
        inventory_router,
        stock_router,
        attendance_router,
        offboard_review_router,
        stocktake_router,
    ],
    workflows=[ONBOARD, OFFBOARD, TRANSFER, REASSIGN_JOB, ASSET_STATE, ASSET_DISPOSE],
    document_kinds=[HANDOVER, RETURN, STOCKTAKE_RECORD],
    # Filed under the shared menu headings by what each page is for (core orders the headings).
    nav=[
        NavItem("Job onboard / offboard", "/lifecycle/jobs", "workflow", "lifecycle.job.view", NAV_WORK),
        NavItem(
            "Đối soát theo Mã NV", "/lifecycle/reconciliation", "git-compare-arrows", RECON_VIEW, NAV_WORK
        ),
        NavItem("Kho thiết bị", "/lifecycle/stock", "package", "lifecycle.job.view", NAV_ASSETS),
        NavItem("Biên bản", "/lifecycle/documents", "file-text", "lifecycle.documents.view", NAV_ASSETS),
        NavItem(
            "Báo cáo chấm công", "/lifecycle/attendance", "clock", "lifecycle.attendance.export", NAV_REVIEWS
        ),
        NavItem(
            "Rà soát nghỉ việc (ISO)",
            "/lifecycle/offboard-review",
            "user-check",
            "lifecycle.offboard_review.export",
            NAV_REVIEWS,
        ),
        NavItem(
            "Kiểm kê tài sản IT",
            "/lifecycle/stocktake",
            "clipboard-check",
            "lifecycle.stocktake.view",
            NAV_REVIEWS,
        ),
        NavItem("Request RTR", "/lifecycle/requests", "ticket", RECON_VIEW, NAV_SOURCES),
        NavItem("Đồng bộ dữ liệu", "/lifecycle/sync", "refresh-cw", RECON_VIEW, NAV_SOURCES),
        # Configuration of the Jobs (steps, checklists): for checklist managers. Operators read each step's checklist in
        # the Job itself and the "how" in the Playbook.
        NavItem("Lưu đồ quy trình", "/lifecycle/flows", "route", "core.workflow.checklist.manage", NAV_SETUP),
        NavItem("Mẫu biên bản", "/lifecycle/templates", "file-cog", "lifecycle.templates.manage", NAV_SETUP),
        NavItem("Cấu hình module", "/lifecycle/settings", "settings", "lifecycle.settings.manage", NAV_SETUP),
    ],
    # Microsoft connectors (entra, ad, exchange) are added once that integration is cleared.
    connectors_used=["sdp", "biostar"],
    facts=FACTS,
    scheduled_tasks=[
        ScheduledTask(
            "lifecycle.sync",
            "Đọc SDP + BioStar và đối soát theo Mã NV",
            _sync,
            interval_seconds=1800,
            timeout_seconds=1800,
        ),
    ],
    next_actions=[
        NextActionSource("lifecycle.jobs", "lifecycle.job.view", next_actions.load, next_actions.summary),
        NextActionSource(
            "lifecycle.offboard_review", "lifecycle.offboard_review.export", offboard_review_action
        ),
        NextActionSource("lifecycle.stocktake", "lifecycle.stocktake.count", stocktake_action),
    ],
    dashboard_widgets=[
        # The team's RTR queue on the main page: technicians take requests themselves (no automatic split);
        # the browser loads the jobs itself so a claim refreshes it at once.
        DashboardWidget(
            "lifecycle.team_queue",
            "Hàng đợi request RTR của team",
            "lifecycle_team_queue",
            "lifecycle.job.execute",
            _empty,
            order=2,  # after the person's own next actions
        ),
        DashboardWidget(
            "lifecycle.jobs",
            "Job onboard / offboard",
            "lifecycle_jobs",
            "lifecycle.job.view_all",
            _jobs,
            order=3,
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
