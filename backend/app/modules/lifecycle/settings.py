"""Module settings (stored via core module settings, edited in the UI by admins)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

EventKind = Literal["onboard", "probation_to_official", "transfer", "offboard", "ignore"]

MODULE_ID = "lifecycle"
SETTINGS_KEY = "main"

DEFAULT_LABELS: dict[str, str] = {
    "change_type": "Loại biến động",
    "full_name": "Họ & tên",
    "employee_id": "Mã nhân viên",
    "effective_date": "Ngày hiệu lực",
    "job_title": "Chức danh nội bộ",
    "job_title_external": "Chức danh bên ngoài",
    "division": "Khối",
    "department": "Phòng",
    "unit": "Bộ phận",
    "work_location": "Nơi làm việc",
    "phone": "Số điện thoại",
    "manager": "Quản lý trực tiếp",
    "indirect_manager": "Quản lý gián tiếp",
}

FIELD_TITLES: dict[str, str] = {
    "change_type": "Loại biến động",
    "full_name": "Họ tên",
    "employee_id": "Mã nhân viên",
    "effective_date": "Ngày hiệu lực",
    "job_title": "Chức danh",
    "job_title_external": "Chức danh bên ngoài",
    "division": "Khối",
    "department": "Phòng",
    "unit": "Bộ phận",
    "work_location": "Nơi làm việc",
    "phone": "Số điện thoại",
    "manager": "Quản lý trực tiếp",
    "indirect_manager": "Quản lý gián tiếp",
}

REQUIRED_FIELDS = ("employee_id", "full_name", "change_type")


class LifecycleSettings(BaseModel):
    # Connections (None = first enabled Production profile of that type)
    sdp_profile_id: uuid.UUID | None = None
    biostar_profile_id: uuid.UUID | None = None

    # RTR requests in ServiceDesk Plus
    rtr_requester_name: str = Field("RTR", min_length=1, max_length=200)
    rtr_lookback_days: int = Field(180, ge=7, le=1095)
    field_labels: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_LABELS))
    # Raw "Loại biến động" value → kind. Values not listed fall back to keyword detection.
    change_type_map: dict[str, EventKind] = Field(default_factory=dict)

    # SDP users and assets
    sdp_user_employee_id_field: str = Field("employee_id", min_length=1, max_length=100)
    match_assets_by_name: bool = True
    asset_product_types: list[str] = Field(
        default_factory=lambda: ["Workstation", "Laptop", "Monitor", "Mouse", "Keyboard"]
    )
    asset_state_in_use: str = "In Use"
    asset_state_in_store: str = "In Store"

    # Reconciliation thresholds
    onboard_grace_days: int = Field(3, ge=0, le=60)
    offboard_grace_days: int = Field(0, ge=0, le=60)
    snapshot_retention_days: int = Field(90, ge=7, le=730)

    # Documents (Phase 3)
    reviewer_name: str = Field("", max_length=200)

    def label(self, field: str) -> str:
        return self.field_labels.get(field) or DEFAULT_LABELS[field]
