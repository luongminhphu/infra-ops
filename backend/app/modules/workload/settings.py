"""Module settings (stored via core module settings, edited in the UI)."""

from __future__ import annotations

import uuid

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.service import load_settings

MODULE_ID = "workload"
SETTINGS_KEY = "main"


DEFAULT_PO_SUBJECT = "[IT] Trình phê duyệt PO {{ po_number }} – {{ total }}"
DEFAULT_PO_BODY = """Kính gửi Anh/Chị Head of IT và Phòng Finance (bộ phận mua hàng),

Bộ phận IT trình phê duyệt {{ po_number }} ngày {{ date }}: {{ item_count }} hạng mục, \
tổng giá trị {{ total }} (hạn mức mỗi PO: {{ limit }}).

{{ items_table }}

Với PC/Laptop, đơn giá là báo giá thấp nhất trong các báo giá so sánh; nhà cung cấp không có hàng \
có ảnh chụp website tại thời điểm hỏi giá.
Đính kèm: bảng chi tiết Excel, file báo giá của nhà cung cấp, ảnh chụp không có hàng và bằng chứng \
Function Head đã duyệt từng đề xuất.

Kính đề nghị Anh/Chị xem xét phê duyệt.

Trân trọng,
{{ closed_by }}"""


class WorkloadSettings(BaseModel):
    # None = first enabled Production SDP profile.
    sdp_profile_id: uuid.UUID | None = None
    # First sync reads requests updated within this window (covers the 1-year statistics).
    lookback_days: int = Field(400, ge=30, le=1100)
    # Only requests of these SDP support groups (empty = every group).
    group_names: list[str] = Field(default_factory=list)
    # SDP status names (case-insensitive) that mean the task is finished / dropped.
    # SDP's standard names (also in Vietnamese) always count – see rules.BUILTIN_*; add custom ones here.
    done_statuses: list[str] = Field(
        default_factory=lambda: ["Resolved", "Closed", "Completed", "Đã xử lý", "Đã đóng"]
    )
    cancelled_statuses: list[str] = Field(
        default_factory=lambda: ["Canceled", "Cancelled", "Rejected", "Đã hủy"]
    )
    # Open tasks split by SDP status: being worked on / paused; any other open status is "pending".
    in_progress_statuses: list[str] = Field(default_factory=lambda: ["In Progress", "Đang xử lý"])
    on_hold_statuses: list[str] = Field(default_factory=lambda: ["On Hold", "Onhold", "Pending", "Tạm dừng"])
    # Words in the subject/category that mark a device request (suggest a purchase request).
    device_keywords: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "replace": ["thay thế", "thay mới", "đổi máy", "cấp mới", "replace", "replacement"],
            "upgrade": ["nâng cấp", "thêm ram", "nâng ram", "ổ ssd", "upgrade"],
            "repair": ["sửa chữa", "sửa máy", "bảo hành", "hỏng", "repair"],
        }
    )
    # SDP asset state given to devices created when purchased goods are received.
    asset_state_in_store: str = Field("In Store", min_length=1, max_length=64)
    # Suggested asset tag on receipt: <prefix>-<year>-<code of the price-list group>-<last 4 of serial>.
    asset_tag_prefix: str = Field("HCM", min_length=1, max_length=20)
    asset_tag_codes: dict[str, str] = Field(
        default_factory=lambda: {
            "Laptop": "LT",
            "PC": "PC",
            "Desktop": "PC",
            "Workstation": "WS",
            "Màn hình": "MN",
        }
    )
    # 3-quote process: items of these price-list groups are priced per proposal from vendor quotes;
    # when the lowest quote is above the threshold, this many quotes are required.
    quote_categories: list[str] = Field(default_factory=lambda: ["PC", "Laptop", "Desktop", "Workstation"])
    quote_threshold_vnd: int = Field(20_000_000, ge=0, le=100_000_000_000)
    quotes_required: int = Field(3, ge=1, le=10)
    # Purchase orders: approved proposals are packed into POs whose value stays within this limit (VND).
    po_limit_vnd: int = Field(200_000_000, ge=1_000_000, le=100_000_000_000)
    # Draft e-mail generated when a PO is closed (.eml, opens in Outlook as an unsent draft).
    po_email_to: list[str] = Field(default_factory=list)
    po_email_cc: list[str] = Field(default_factory=list)
    po_email_subject: str = Field(DEFAULT_PO_SUBJECT, min_length=3, max_length=300)
    po_email_body: str = Field(DEFAULT_PO_BODY, min_length=10, max_length=10000)

    @field_validator("po_email_subject", "po_email_body")
    @classmethod
    def _template(cls, value: str) -> str:
        try:
            SandboxedEnvironment().parse(value)
        except TemplateSyntaxError as exc:
            raise ValueError(f"Mẫu email sai cú pháp (dòng {exc.lineno}): {exc.message}") from exc
        return value


async def load(session: AsyncSession) -> WorkloadSettings:
    return await load_settings(session, MODULE_ID, SETTINGS_KEY, WorkloadSettings)
