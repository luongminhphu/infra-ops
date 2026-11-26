"""Monthly review of people who left (ISO 27001 A.5.18 / A.6.5: access rights removed on termination).

IT lists every offboard RTR request effective in the month with what each system still holds, and sends HR a
draft e-mail with the list attached, so HR confirms the list is complete and correct."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import escape
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.models import AuditLog
from app.core.branding.service import xlsx_logo
from app.core.timeutil import TZ
from app.core.workflow.models import Job
from app.modules.lifecycle.models import LifecycleRequest, ReconRow
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import LifecycleSettings

JOB_LABEL = {
    "draft": "Nháp",
    "scheduled": "Đã lên lịch",
    "running": "Đang chạy",
    "waiting": "Đang chờ",
    "failed": "Lỗi",
    "completed": "Hoàn tất",
    "cancelled": "Đã huỷ",
}
# SDP request statuses as HR reads them (others are shown as SDP names them).
SDP_STATUS_VN = {
    "open": "Đang mở",
    "in progress": "Đang xử lý",
    "on hold": "Tạm dừng",
    "resolved": "Đã xử lý",
    "closed": "Đã đóng",
    "cancelled": "Đã huỷ",
    "canceled": "Đã huỷ",
}
HEADERS = [
    "STT",
    "Mã NV",
    "Họ tên",
    "Phòng ban",
    "Ngày nghỉ việc",
    "Request RTR",
    "Job offboard",
    "Tài sản còn gán",
    "BioStar",
    "Kết luận IT",
]


@dataclass
class ReviewRow:
    employee_id: str
    full_name: str
    department: str
    effective_date: date
    request: str
    request_status: str
    job_status: str | None
    job_id: str | None
    assets_in_use: int | None  # None = not read
    biostar: str
    biostar_active: bool | None

    @property
    def done(self) -> bool:
        return self.job_status == "completed" and self.assets_in_use == 0 and self.biostar_active is False

    @property
    def conclusion(self) -> str:
        if self.done:
            return "Đã thu hồi quyền truy cập và tài sản"
        todo = []
        if self.job_status != "completed":
            todo.append("Job offboard chưa hoàn tất")
        if self.assets_in_use:
            todo.append(f"còn {self.assets_in_use} tài sản")
        if self.biostar_active:
            todo.append("BioStar còn hoạt động")
        return "Cần xử lý: " + ", ".join(todo) if todo else "Chưa đủ dữ liệu đối soát"

    def to_dict(self) -> dict[str, Any]:
        return {
            "employee_id": self.employee_id,
            "full_name": self.full_name,
            "department": self.department,
            "effective_date": self.effective_date.isoformat(),
            "request": self.request,
            "request_status": self.request_status,
            "job_status": self.job_status,
            "job_id": self.job_id,
            "assets_in_use": self.assets_in_use,
            "biostar": self.biostar,
            "biostar_active": self.biostar_active,
            "done": self.done,
            "conclusion": self.conclusion,
        }


def month_range(month: date) -> tuple[date, date]:
    first = month.replace(day=1)
    return first, (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)


async def review_rows(session: AsyncSession, month: date) -> list[ReviewRow]:
    first, last = month_range(month)
    reqs = (
        await session.scalars(
            select(LifecycleRequest)
            .where(
                LifecycleRequest.kind == "offboard",
                LifecycleRequest.effective_date >= first,
                LifecycleRequest.effective_date <= last,
                LifecycleRequest.employee_id.is_not(None),
            )
            .order_by(LifecycleRequest.effective_date, LifecycleRequest.employee_id)
        )
    ).all()
    emps = [r.employee_id for r in reqs if r.employee_id]
    run = await latest_run(session)
    recon: dict[str, dict[str, Any]] = {}
    if run is not None and emps:
        recon = {
            r.employee_id: r.data
            for r in (
                await session.scalars(
                    select(ReconRow).where(ReconRow.run_id == run.id, ReconRow.employee_id.in_(emps))
                )
            ).all()
        }
    jobs: dict[str, tuple[str, str]] = {}
    if emps:
        for j in (
            await session.scalars(
                select(Job)
                .where(Job.workflow_key == "lifecycle.offboard", Job.subject_key.in_(emps))
                .order_by(Job.created_at)
            )
        ).all():
            jobs[j.subject_key] = (j.status, str(j.id))  # latest job wins
    rows = []
    for r in reqs:
        emp = r.employee_id or ""
        cells = recon.get(emp, {}).get("cells", {})
        assets = cells.get("assets", {})
        bs = cells.get("biostar", {})
        in_use = None
        if assets and assets.get("status") != "skipped":
            in_use = int(str(assets.get("label", "0")).split()[0] or 0)
        bs_active = None if not bs or bs.get("status") == "skipped" else bs.get("label") == "Hoạt động"
        rows.append(
            ReviewRow(
                employee_id=emp,
                full_name=r.full_name or "",
                department=(r.fields or {}).get("department") or "",
                effective_date=r.effective_date or first,
                request=f"#{r.display_id or r.id}",
                request_status=SDP_STATUS_VN.get((r.status or "").lower(), r.status or ""),
                job_status=jobs[emp][0] if emp in jobs else None,
                job_id=jobs[emp][1] if emp in jobs else None,
                assets_in_use=in_use,
                biostar=bs.get("label") or "Chưa đọc",
                biostar_active=bs_active,
            )
        )
    return rows


def _cells(i: int, r: ReviewRow) -> list[Any]:
    return [
        i,
        r.employee_id,
        r.full_name,
        r.department,
        r.effective_date.strftime("%d/%m/%Y"),
        f"{r.request} ({r.request_status})" if r.request_status else r.request,
        JOB_LABEL.get(r.job_status or "", "Chưa có Job"),
        "Chưa đọc" if r.assets_in_use is None else r.assets_in_use,
        r.biostar,
        r.conclusion,
    ]


def _safe(v: Any) -> Any:
    return f"'{v}" if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v


def to_xlsx(rows: list[ReviewRow], month: date, logo: bytes | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = f"Nghỉ việc {month:%m-%Y}"
    ws.append([*HEADERS, "HR xác nhận", "Ghi chú HR"])
    for i, r in enumerate(rows, 1):
        ws.append([_safe(v) for v in _cells(i, r)] + ["", ""])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E3F0F1")
    for i, w in enumerate([5, 10, 26, 22, 13, 22, 14, 10, 14, 42, 14, 30], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "C2"
    xlsx_logo(ws, logo)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def render_email(rows: list[ReviewRow], month: date, sender: str) -> tuple[str, str, str]:
    """Subject, plain text and HTML body asking HR to confirm the list (complete, correct, nobody missing)."""
    m = f"{month:%m/%Y}"
    first, last = month_range(month)
    subject = f"[ISO 27001] Rà soát nhân sự nghỉ việc tháng {m}"
    pending = sum(1 for r in rows if not r.done)
    intro = [
        "Kính gửi anh/chị phòng Nhân sự,",
        "",
        f"Theo quy trình rà soát quyền truy cập định kỳ (ISO 27001), IT gửi danh sách {len(rows)} nhân sự nghỉ việc "
        f"có ngày hiệu lực từ {first:%d/%m/%Y} đến {last:%d/%m/%Y} theo các request RTR, kèm tình trạng thu hồi "
        "quyền truy cập và tài sản.",
        f"Còn {pending} trường hợp IT đang xử lý."
        if pending
        else "IT đã thu hồi đủ cho tất cả các trường hợp.",
        "",
        "Nhờ anh/chị rà soát và phản hồi:",
        "1. Danh sách đã đầy đủ chưa (có nhân sự nghỉ việc trong tháng nào chưa có request RTR không)?",
        "2. Họ tên, phòng ban, ngày nghỉ việc đã đúng chưa?",
        "Anh/chị có thể ghi vào cột “HR xác nhận” / “Ghi chú HR” trong file đính kèm và gửi lại.",
    ]
    outro = ["", "Trân trọng,", sender or "IT Infra Support"]
    text_rows = [
        f"{i}. {r.employee_id} – {r.full_name} – {r.department} – nghỉ {r.effective_date:%d/%m/%Y} – {r.conclusion}"
        for i, r in enumerate(rows, 1)
    ]
    body = "\n".join(intro + [""] + (text_rows or ["(Không có nhân sự nghỉ việc trong tháng.)"]) + outro)
    td = 'style="border:1px solid #c8ced8;padding:4px 8px;vertical-align:top"'
    th = 'style="border:1px solid #c8ced8;padding:4px 8px;background:#eef2f8;text-align:left"'
    table = (
        '<table style="border-collapse:collapse;font-size:13px"><tr>'
        + "".join(f"<th {th}>{h}</th>" for h in HEADERS)
        + "</tr>"
        + "".join(
            "<tr>" + "".join(f"<td {td}>{escape(str(v))}</td>" for v in _cells(i, r)) + "</tr>"
            for i, r in enumerate(rows, 1)
        )
        + "</table>"
    )
    html = (
        "".join(f"<p>{escape(line)}</p>" if line else "" for line in intro)
        + (table if rows else "<p>(Không có nhân sự nghỉ việc trong tháng.)</p>")
        + "".join(f"<p>{escape(line)}</p>" for line in outro if line)
    )
    return subject, body, html


def build_eml(rows: list[ReviewRow], month: date, settings: LifecycleSettings, sender: str) -> bytes:
    """Unsent draft (X-Unsent: Outlook opens it ready to send) with the review workbook attached."""
    subject, body, html = render_email(rows, month, sender)
    msg = EmailMessage()
    msg["Subject"] = subject
    if settings.hr_review_to:
        msg["To"] = ", ".join(settings.hr_review_to)
    if settings.hr_review_cc:
        msg["Cc"] = ", ".join(settings.hr_review_cc)
    msg["X-Unsent"] = "1"
    msg.set_content(body)
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(
        to_xlsx(rows, month),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"ra-soat-nghi-viec-{month:%Y-%m}.xlsx",
    )
    return bytes(msg)


def last_month(now: datetime | None = None) -> date:
    today = (now or datetime.now(TZ)).astimezone(TZ).date()
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


async def next_action(session: AsyncSession, user: Any) -> list[dict[str, Any]]:
    """Monthly reminder for the "Việc tiếp theo" queue until someone downloads last month's e-mail draft."""
    m = last_month()
    sent = await session.scalar(
        select(AuditLog.id)
        .where(
            AuditLog.action == "lifecycle.offboard_review_exported",
            AuditLog.target_id == f"{m:%Y-%m}",
            AuditLog.target_label == "eml",
        )
        .limit(1)
    )
    if sent:
        return []
    return [
        {
            "key": f"offboard-review:{m:%Y-%m}",
            "kind": "review",
            "priority": 2,
            "title": f"Gửi HR rà soát nhân sự nghỉ việc tháng {m:%m/%Y} (ISO 27001)",
            "detail": "Kiểm tra danh sách, tải email nháp kèm file Excel và gửi phòng Nhân sự.",
            "due": None,
            "href": f"/lifecycle/offboard-review?month={m:%Y-%m}",
            "action": "Rà soát",
        }
    ]
