"""Monthly review of people who left (ISO 27001 A.5.18 / A.6.5: access rights removed on termination).

IT lists every offboard RTR request effective in the month with what each system still holds, and sends HR a
draft e-mail with the list attached, so HR confirms the list is complete and correct."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email import policy
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
# What HR checks against their own records - IT's working detail (Job, assets, BioStar) stays on the IT page.
HEADERS = [
    "STT",
    "Mã NV",
    "Họ tên",
    "Phòng ban",
    "Ngày nghỉ việc",
    "Request ITSM",
    "Trạng thái xử lý",
    "Ngày IT hoàn tất",
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
    closed_on: date | None = None  # the day IT completed the offboard Job (Vietnam time)

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

    @property
    def remaining(self) -> list[str]:
        """What is still open, in words HR understands (the Job itself is IT's business)."""
        out = []
        if self.assets_in_use:
            out.append(f"còn {self.assets_in_use} tài sản")
        if self.biostar_active:
            out.append("thẻ / vân tay ra vào còn hiệu lực")
        return out

    @property
    def hr_status(self) -> str:
        if self.done:
            return "Đã thu hồi"
        return "Đang xử lý" + (f" ({', '.join(self.remaining)})" if self.remaining else "")

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
            "hr_status": self.hr_status,
            "closed_on": self.closed_on.isoformat() if self.closed_on else None,
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
    jobs: dict[str, tuple[str, str, date | None]] = {}
    if emps:
        for j in (
            await session.scalars(
                select(Job)
                .where(Job.workflow_key == "lifecycle.offboard", Job.subject_key.in_(emps))
                .order_by(Job.created_at)
            )
        ).all():
            closed = j.closed_at.astimezone(TZ).date() if j.closed_at and j.status == "completed" else None
            jobs[j.subject_key] = (j.status, str(j.id), closed)  # latest job wins
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
                closed_on=jobs[emp][2] if emp in jobs else None,
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
        r.request,
        r.hr_status,
        r.closed_on.strftime("%d/%m/%Y") if r.closed_on and r.done else "",
    ]


def _safe(v: Any) -> Any:
    return f"'{v}" if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v


def to_xlsx(rows: list[ReviewRow], month: date, logo: bytes | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = f"Nghỉ việc {month:%m-%Y}"
    ws.append([*HEADERS, "HR xác nhận (Đúng / Sai)", "Ghi chú HR"])
    for i, r in enumerate(rows, 1):
        ws.append([_safe(v) for v in _cells(i, r)] + ["", ""])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E3F0F1")
    for i, w in enumerate([5, 10, 28, 24, 14, 14, 34, 14, 16, 32], start=1):
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
    """Subject, plain text and HTML of a short request: HR confirms the list is complete and correct."""
    m = f"{month:%m/%Y}"
    subject = f"[ISO 27001] Xác nhận danh sách nhân sự nghỉ việc tháng {m}"
    done = sum(r.done for r in rows)
    if done == len(rows):
        status = f"IT đã thu hồi quyền truy cập và tài sản của cả {done} trường hợp."
    elif done == 0:
        status = f"Cả {len(rows)} trường hợp IT đang xử lý."
    else:
        status = f"Tình trạng: {done} đã thu hồi quyền truy cập và tài sản, {len(rows) - done} đang xử lý."
    lead = (
        f"IT gửi danh sách {len(rows)} nhân sự nghỉ việc tháng {m} (theo request ITSM) để rà soát quyền truy cập "
        "định kỳ theo ISO 27001."
        if rows
        else f"Theo request ITSM, tháng {m} không có nhân sự nghỉ việc. Nhờ anh/chị xác nhận giúp IT."
    )
    asks = [
        "Danh sách đã đủ – không thiếu nhân sự nghỉ việc nào trong tháng.",
        "Họ tên, phòng ban và ngày nghỉ việc đúng.",
    ]
    reply = "Vui lòng điền cột “HR xác nhận” trong file đính kèm và phản hồi email này."
    sign = sender or "IT Infra Support"

    text = [
        "Kính gửi phòng Nhân sự,",
        "",
        lead + (f" {status}" if rows else ""),
        "",
        *(
            ["Nhờ anh/chị xác nhận:", *(f"{n}. {a}" for n, a in enumerate(asks, 1)), reply, ""]
            if rows
            else []
        ),
        *(
            f"{i}. {r.employee_id} – {r.full_name} – {r.department or '—'} – nghỉ {r.effective_date:%d/%m/%Y} – "
            f"{r.request} – {r.hr_status}"
            for i, r in enumerate(rows, 1)
        ),
        "",
        "Trân trọng,",
        sign,
    ]

    font = "font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;line-height:1.5"
    th = "padding:6px 10px;border:1px solid #d5dbe3;background:#eef2f7;text-align:left;font-weight:600"
    td = "padding:6px 10px;border:1px solid #d5dbe3;vertical-align:top"

    def cell(v: Any, extra: str = "") -> str:
        return f'<td style="{td}{extra}">{escape(str(v))}</td>'

    def row_html(i: int, r: ReviewRow) -> str:
        colour = "color:#166534" if r.done else "color:#92400e"
        c = _cells(i, r)
        nowrap = ";white-space:nowrap"
        return (
            "<tr>"
            + "".join(cell(v, nowrap if k in (0, 1, 4, 5) else "") for k, v in enumerate(c[:6]))
            + cell(c[6], f";{colour};font-weight:600")
            + cell(c[7], nowrap)
            + "</tr>"
        )

    parts = [
        f'<div style="{font}">',
        "<p>Kính gửi phòng Nhân sự,</p>",
        f"<p>{escape(lead)}" + (f"<br>{escape(status)}" if rows else "") + "</p>",
    ]
    if rows:
        parts += [
            "<p>Nhờ anh/chị xác nhận:</p>",
            '<ol style="margin:0 0 12px 20px;padding:0">',
            *(f"<li>{escape(a)}</li>" for a in asks),
            "</ol>",
            f"<p>{escape(reply)}</p>",
            '<table style="border-collapse:collapse;font-size:13px;margin:8px 0 16px">',
            "<tr>" + "".join(f'<th style="{th}">{escape(h)}</th>' for h in HEADERS) + "</tr>",
            *(row_html(i, r) for i, r in enumerate(rows, 1)),
            "</table>",
        ]
    parts += [f"<p>Trân trọng,<br>{escape(sign)}</p>", "</div>"]
    return subject, "\n".join(text), "\n".join(parts)


def build_eml(rows: list[ReviewRow], month: date, settings: LifecycleSettings, sender: str) -> bytes:
    """Unsent draft (X-Unsent: Outlook opens it ready to send) with the review workbook attached.

    Bodies are base64 with CRLF line ends: Outlook mangled long quoted-printable lines (broken accents, stray "=")."""
    subject, body, html = render_email(rows, month, sender)
    msg = EmailMessage(policy=policy.SMTP)
    msg["Subject"] = subject
    if settings.hr_review_to:
        msg["To"] = ", ".join(settings.hr_review_to)
    if settings.hr_review_cc:
        msg["Cc"] = ", ".join(settings.hr_review_cc)
    msg["X-Unsent"] = "1"
    msg.set_content(body, cte="base64")
    msg.add_alternative(html, subtype="html", cte="base64")
    msg.add_attachment(
        to_xlsx(rows, month),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"ra-soat-nghi-viec-{month:%Y-%m}.xlsx",
    )
    return msg.as_bytes()


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
