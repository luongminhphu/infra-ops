"""Module settings (stored via core module settings, edited in the UI)."""

from __future__ import annotations

import uuid

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, Field, field_validator

MODULE_ID = "workload"
SETTINGS_KEY = "main"


DEFAULT_PO_SUBJECT = "[IT] Đề nghị mua thiết bị {{ po_number }} – {{ total }}"
DEFAULT_PO_BODY = """Kính gửi Phòng Mua hàng,

Bộ phận IT đề nghị mua thiết bị theo {{ po_number }} ngày {{ date }}, gồm {{ item_count }} hạng mục, \
tổng giá trị dự kiến {{ total }} (hạn mức mỗi PO: {{ limit }}).

Chi tiết:
{{ items_text }}

Bảng chi tiết và bằng chứng phê duyệt của Function Head được đính kèm email này.

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
    done_statuses: list[str] = Field(default_factory=lambda: ["Resolved", "Closed", "Completed"])
    cancelled_statuses: list[str] = Field(default_factory=lambda: ["Canceled", "Cancelled", "Rejected"])
    # Open tasks split by SDP status: being worked on / paused; any other open status is "pending".
    in_progress_statuses: list[str] = Field(default_factory=lambda: ["In Progress", "Đang xử lý"])
    on_hold_statuses: list[str] = Field(default_factory=lambda: ["On Hold", "Onhold", "Pending", "Tạm dừng"])
    # Create an infra-ops account (role Operator, SSO login) for active technicians that have none.
    auto_create_users: bool = True
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
