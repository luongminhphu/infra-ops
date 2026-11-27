"""Monthly offboarding review for HR (ISO 27001): list, Excel, e-mail draft, reminder."""

from __future__ import annotations

import email
import io
from datetime import date
from email import policy

from openpyxl import load_workbook

from app.core.db import get_sessionmaker
from app.modules.lifecycle.offboard_review import ReviewRow, last_month, next_action, render_email


def test_conclusion_and_email_text() -> None:
    base = dict(full_name="Phạm Thị Bình", department="Kế Toán", effective_date=date(2026, 10, 5),
                request="#7902", request_status="Đã xử lý", job_id=None)  # fmt: skip
    done = ReviewRow(
        "002100", **base, job_status="completed", assets_in_use=0, biostar="Không có", biostar_active=False
    )  # type: ignore[arg-type]
    todo = ReviewRow(
        "002101", **base, job_status="draft", assets_in_use=2, biostar="Hoạt động", biostar_active=True
    )  # type: ignore[arg-type]
    assert done.done and done.conclusion == "Đã thu hồi quyền truy cập và tài sản"
    assert todo.conclusion == "Cần xử lý: Job offboard chưa hoàn tất, còn 2 tài sản, BioStar còn hoạt động"
    # HR sees only what they can check: no Job / BioStar jargon, the open items in plain words.
    assert done.hr_status == "Đã thu hồi"
    assert todo.hr_status == "Đang xử lý (còn 2 tài sản, thẻ / vân tay ra vào còn hiệu lực)"
    subject, body, html = render_email([done, todo], date(2026, 10, 1), "An")
    assert subject == "[ISO 27001] Xác nhận danh sách nhân sự nghỉ việc tháng 10/2026"
    assert "Tình trạng: 1 đã thu hồi quyền truy cập và tài sản, 1 đang xử lý." in body
    assert "Cả 1 trường hợp IT đang xử lý." in render_email([todo], date(2026, 10, 1), "An")[1]
    assert "Request ITSM" in html and "Job offboard" not in html and "BioStar" not in html
    assert "<table" in html and "Phạm Thị Bình" in html


async def test_review_lists_month_offboards_and_drafts_the_email(admin) -> None:  # type: ignore[no-untyped-def]
    from app.modules.lifecycle.models import LifecycleRequest

    m = last_month()
    # Settings first: saving them re-parses stored requests (these test rows have no RTR description).
    values = (await admin.get("/api/lifecycle/settings")).json()["values"]
    values["hr_review_to"] = ["hr@congty.local"]
    assert (await admin.put("/api/lifecycle/settings", json=values)).status_code == 200
    async with get_sessionmaker()() as s:
        for rid, kind, emp, day in (("r-off", "offboard", "777001", 10), ("r-on", "onboard", "777002", 11)):
            s.add(LifecycleRequest(id=rid, display_id=rid, subject="x", status="Open", employee_id=emp,
                                   full_name=f"NV {emp}", kind=kind, effective_date=m.replace(day=day),
                                   fields={"department": "IT"}))  # fmt: skip
        await s.commit()

    got = (await admin.get("/api/lifecycle/offboard-review")).json()  # default: last month
    assert got["month"] == f"{m:%Y-%m}" and [r["employee_id"] for r in got["items"]] == ["777001"]
    assert got["items"][0]["job_status"] is None and got["pending"] == 1
    assert got["items"][0]["request_status"] == "Đang mở"  # SDP status in Vietnamese for HR

    async with get_sessionmaker()() as s:
        assert await next_action(s, None)  # reminder until the draft is downloaded
    resp = await admin.get("/api/lifecycle/offboard-review/export", params={"month": f"{m:%Y-%m}"})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("message/rfc822")
    # Outlook-safe: CRLF lines no longer than 998 bytes, bodies base64 (no quoted-printable "=C3" debris).
    assert b"\r\n" in resp.content and all(len(line) <= 998 for line in resp.content.split(b"\r\n"))
    assert b"quoted-printable" not in resp.content
    msg = email.message_from_bytes(resp.content, policy=policy.default)
    assert msg["To"] == "hr@congty.local" and msg["X-Unsent"] == "1"
    html = msg.get_body(("html",)).get_content()
    assert "Kính gửi phòng Nhân sự," in html and "NV 777001" in html and "Đang xử lý" in html
    assert f"tháng {m:%m/%Y}" in msg["Subject"]
    att = next(p for p in msg.iter_attachments())
    ws = load_workbook(io.BytesIO(att.get_content())).active
    assert ws is not None and ws["B2"].value == "777001" and ws["F1"].value == "Request ITSM"
    assert ws["I1"].value == "HR xác nhận (Đúng / Sai)" and ws["G2"].value == "Đang xử lý"
    async with get_sessionmaker()() as s:
        assert await next_action(s, None) == []

    xlsx = await admin.get(
        "/api/lifecycle/offboard-review/export", params={"month": f"{m:%Y-%m}", "format": "xlsx"}
    )
    assert xlsx.status_code == 200
    assert (await admin.get("/api/lifecycle/offboard-review", params={"month": "2026-13"})).status_code == 400
