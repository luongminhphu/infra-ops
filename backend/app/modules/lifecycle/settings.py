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
    "band": "Band",
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
    "band": "Band",
}

REQUIRED_FIELDS = ("employee_id", "full_name", "change_type")


class StandardItem(BaseModel):
    """One line of the device allocation standard: what a new employee of these bands receives."""

    label: str = Field(min_length=1, max_length=100)  # e.g. "Laptop tiêu chuẩn"
    bands: list[str] = Field(default_factory=list)  # empty = every band
    product_types: list[str] = Field(min_length=1)  # SDP product types that satisfy this line
    product_contains: str = Field("", max_length=100)  # optional: model must contain this text
    quantity: int = Field(1, ge=1, le=10)
    catalog_name: str = Field("", max_length=300)  # price-list item to propose when buying new


DEFAULT_STANDARD = [
    StandardItem(label="Laptop", product_types=["Laptop"], catalog_name=""),
    StandardItem(label="Màn hình", product_types=["Monitor"]),
    StandardItem(label="Chuột", product_types=["Mouse"]),
    StandardItem(label="Bàn phím", product_types=["Keyboard"]),
]


class BioStarSource(BaseModel):
    """An extra BioStar system read for reconciliation only (e.g. an old office server).

    Its user IDs may use another prefix for the employee ID: user ``100002`` with prefix ``1`` → ``0`` is employee
    ``000002``. Jobs still write only to the main BioStar connection.
    """

    profile_id: uuid.UUID
    id_prefix: str = Field("", max_length=8)
    id_prefix_to: str = Field("", max_length=8)


class LifecycleSettings(BaseModel):
    # Connections (None = first enabled Production profile of that type)
    sdp_profile_id: uuid.UUID | None = None
    biostar_profile_id: uuid.UUID | None = None
    # More BioStar systems read for reconciliation (each with its own employee-ID prefix rule).
    biostar_sources: list[BioStarSource] = Field(default_factory=list, max_length=5)

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

    # Device allocation standard by band (inventory check for onboard requests)
    allocation_standard: list[StandardItem] = Field(
        default_factory=lambda: [i.model_copy() for i in DEFAULT_STANDARD]
    )
    # Onboard requests whose effective date is older than this are no longer planned.
    allocation_past_days: int = Field(30, ge=0, le=365)

    # Documents (Phase 3)
    reviewer_name: str = Field("", max_length=200)
    # Workflows (Onboard / Offboard)
    auto_create_jobs: bool = True
    sdp_close_status: str = Field("Resolved", min_length=1, max_length=64)
    sdp_closed_statuses: list[str] = Field(
        default_factory=lambda: ["Closed", "Resolved", "Cancelled", "Canceled"]
    )
    sdp_add_note: bool = True
    biostar_user_group_id: str = Field("1", max_length=32)
    biostar_access_group_ids: list[str] = Field(default_factory=list)
    biostar_expiry_date: str = Field("2037-12-31", pattern=r"^\d{4}-\d{2}-\d{2}$")
    biostar_offboard_action: Literal["disable", "delete"] = "disable"

    # SDP attachment upload (verify against your SDP build; editable without code change)
    sdp_attach_path: str = Field("/api/v3/requests/{request_id}/_uploads", max_length=200)
    sdp_attach_field: str = Field("filename", max_length=50)

    def label(self, field: str) -> str:
        return self.field_labels.get(field) or DEFAULT_LABELS[field]
