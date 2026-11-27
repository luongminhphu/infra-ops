"""Module settings (stored via core module settings, edited in the UI by admins)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.service import load_settings

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
    # Derived from the allocation matrix (empty for hand-written lines): the matrix row it comes from, the
    # departments that row is limited to, and for the "other bands" row the bands other rows already cover.
    level: str = Field("", max_length=100)
    departments: list[str] = Field(default_factory=list)
    except_bands: list[str] = Field(default_factory=list)
    tier: str = Field("", max_length=100)


class DeviceTier(BaseModel):
    """A reference configuration (e.g. "PC - Performance") a matrix cell can name; shown to whoever buys or picks."""

    name: str = Field(min_length=1, max_length=100)
    cpu: str = Field("", max_length=200)
    ram: str = Field("", max_length=50)
    storage: str = Field("", max_length=50)
    description: str = Field("", max_length=300)
    catalog_name: str = Field("", max_length=300)  # price-list item proposed when this tier must be bought


class MatrixDevice(BaseModel):
    """A matrix column: a kind of device and how SDP / the price list name it."""

    label: str = Field(min_length=1, max_length=100)
    product_types: list[str] = Field(min_length=1, max_length=10)
    product_contains: str = Field("", max_length=100)
    catalog_name: str = Field("", max_length=300)
    note: str = Field("", max_length=300)  # policy shown under the column, e.g. "Theo nhu cầu công việc"


class MatrixLevel(BaseModel):
    """A matrix row: a band group (bands as written in the RTR request), optionally limited to departments."""

    name: str = Field(min_length=1, max_length=100)
    bands: list[str] = Field(default_factory=list, max_length=40)  # empty = every band no other row lists
    departments: list[str] = Field(default_factory=list, max_length=40)  # empty = every department
    group: str = Field("", max_length=200)  # who this is, e.g. "Manager / Senior Manager"


class MatrixCell(BaseModel):
    on: bool = False
    quantity: int = Field(1, ge=1, le=10)
    tier: str = Field("", max_length=100)


class AllocationMatrix(BaseModel):
    """Rows = band groups, columns = devices, cells = what that group receives (cells[row][column])."""

    devices: list[MatrixDevice] = Field(default_factory=list, max_length=30)
    levels: list[MatrixLevel] = Field(default_factory=list, max_length=30)
    cells: list[list[MatrixCell]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> AllocationMatrix:
        if len(self.cells) != len(self.levels) or any(len(r) != len(self.devices) for r in self.cells):
            raise ValueError("Ma trận cấp phát không khớp số hàng / cột.")
        fallback = [lv.name for lv in self.levels if not lv.bands]
        if len(fallback) > 1:
            raise ValueError(
                f"Chỉ một hàng được để trống band (áp dụng cho band còn lại): {', '.join(fallback)}."
            )
        return self

    def standard(self) -> list[StandardItem]:
        """The allocation lines the engine uses: one per switched-on cell, named by its tier when it has one (e.g.
        "PC - Performance"); same device + tier + quantity in rows with the same departments share a line (the
        union of their bands)."""
        # Bands a general row lists: the "other bands" row skips them (department rows do not count).
        listed = sorted({b for lv in self.levels if not lv.departments for b in lv.bands})
        lines: dict[tuple[Any, ...], StandardItem] = {}
        for lv, row in zip(self.levels, self.cells, strict=True):
            for dev, cell in zip(self.devices, row, strict=True):
                if not cell.on:
                    continue
                key = (dev.label, cell.tier, cell.quantity, tuple(lv.departments), not lv.bands)
                item = lines.get(key)
                if item is None:
                    lines[key] = StandardItem(
                        label=cell.tier or dev.label, bands=list(lv.bands), product_types=dev.product_types,
                        product_contains=dev.product_contains, quantity=cell.quantity,
                        catalog_name=dev.catalog_name, level=lv.name, departments=list(lv.departments),
                        except_bands=listed if not lv.bands else [], tier=cell.tier,
                    )  # fmt: skip
                else:
                    item.bands = list(dict.fromkeys([*item.bands, *lv.bands]))
                    item.level = f"{item.level}, {lv.name}"
        return list(lines.values())


def matrix_from_standard(items: list[StandardItem]) -> AllocationMatrix:
    """A first matrix from hand-written lines (shown until the matrix is saved): one column per line label, one
    row per band list used, and a row for every other band."""
    devices: list[MatrixDevice] = []
    for i in items:
        if i.label not in {d.label for d in devices}:
            devices.append(MatrixDevice(label=i.label, product_types=i.product_types or [i.label],
                                        product_contains=i.product_contains, catalog_name=i.catalog_name))  # fmt: skip
    band_sets = list(dict.fromkeys(tuple(i.bands) for i in items if i.bands))
    levels = [MatrixLevel(name=", ".join(b)[:100], bands=list(b)) for b in band_sets]
    levels.append(MatrixLevel(name="Các band khác", group="Band không nằm ở hàng nào ở trên"))
    cells = []
    for lv in levels:
        row = []
        for d in devices:
            hit = next(
                (
                    i
                    for i in items
                    if i.label == d.label and (not i.bands or tuple(i.bands) == tuple(lv.bands))
                ),
                None,
            )
            row.append(MatrixCell(on=hit is not None, quantity=hit.quantity if hit else 1))
        cells.append(row)
    return AllocationMatrix(devices=devices, levels=levels, cells=cells)


DEFAULT_TIERS = [
    DeviceTier(name="PC - Standard", cpu="Core i3 / Ryzen 3 hoặc tương đương", ram="32 GB", storage="512 GB SSD",
               description="Ứng dụng văn phòng và ứng dụng nghiệp vụ"),
    DeviceTier(name="PC - Performance", cpu="Core i5 / Ryzen 5 / Apple M-series hoặc tương đương", ram="32 GB",
               storage="512 GB SSD", description="Phần mềm nghiệp vụ nặng về xử lý, tính toán"),
    DeviceTier(name="Laptop - Standard", cpu="Core Ultra 5 / Ryzen AI 5 / Apple M-series hoặc tương đương",
               ram="32 GB", storage="512 GB SSD", description="Mỏng, nhẹ, di chuyển nhiều, tác vụ thông thường"),
    DeviceTier(name="Laptop - Performance", cpu="Core i7 / Ultra 7 / Ryzen 7 / Apple M-series Pro hoặc tương đương",
               ram="32 GB", storage="512 GB SSD", description="Phân tích dữ liệu, trình diễn báo cáo"),
]  # fmt: skip


DEFAULT_STANDARD = [
    StandardItem(label="Laptop", product_types=["Laptop"], catalog_name=""),
    StandardItem(label="Màn hình", product_types=["Monitor"]),
    StandardItem(label="Chuột", product_types=["Mouse"]),
    StandardItem(label="Bàn phím", product_types=["Keyboard"]),
]


class BioStarSource(BaseModel):
    """An extra BioStar system read for reconciliation only. Jobs still write only to the main BioStar."""

    profile_id: uuid.UUID


class LifecycleSettings(BaseModel):
    # Connections (None = first enabled Production profile of that type)
    sdp_profile_id: uuid.UUID | None = None
    biostar_profile_id: uuid.UUID | None = None
    # More BioStar systems read for reconciliation (each with its own employee-ID prefix rule).
    biostar_sources: list[BioStarSource] = Field(default_factory=list, max_length=5)
    # BioStar user groups left out of reconciliation: people who left (moved there instead of deleted), vendors,
    # contractors… Their users count as without access and are never reported as active or unknown.
    biostar_excluded_groups: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="before")
    @classmethod
    def _merge_retired_groups(cls, data: Any) -> Any:
        """Settings saved before the two lists were merged kept the "left the company" groups apart."""
        if isinstance(data, dict) and data.get("biostar_retired_groups"):
            data = dict(data)
            retired = data.pop("biostar_retired_groups")
            data["biostar_excluded_groups"] = list(
                dict.fromkeys([*retired, *data.get("biostar_excluded_groups", [])])
            )
        return data

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
    # Stock management: SDP asset states for "waiting for disposal" and "disposed" (must exist on SDP).
    asset_state_pending_disposal: str = Field("Chờ thanh lý", min_length=1, max_length=64)
    asset_state_disposed: str = Field("Disposed", min_length=1, max_length=64)
    # SDP asset custom field (udf_fields key, e.g. "udf_char1") holding the accounting fixed-asset code (Mã TSCĐ).
    asset_fa_code_field: str = Field("", max_length=100, pattern=r"^[A-Za-z0-9_]*$")

    # Reconciliation thresholds
    onboard_grace_days: int = Field(3, ge=0, le=60)
    offboard_grace_days: int = Field(0, ge=0, le=60)
    snapshot_retention_days: int = Field(90, ge=7, le=730)

    # Device allocation standard by band (inventory check for onboard requests)
    allocation_standard: list[StandardItem] = Field(
        default_factory=lambda: [i.model_copy() for i in DEFAULT_STANDARD]
    )
    # Edited in the UI; once saved, allocation_standard is derived from it (None = hand-written lines only).
    allocation_matrix: AllocationMatrix | None = None
    device_tiers: list[DeviceTier] = Field(
        default_factory=lambda: [t.model_copy() for t in DEFAULT_TIERS], max_length=30
    )
    # Onboard requests whose effective date is older than this are no longer planned.
    allocation_past_days: int = Field(30, ge=0, le=365)

    # Documents (Phase 3)
    reviewer_name: str = Field("", max_length=200)
    # Monthly offboarding review sent to HR (ISO 27001 access review): e-mail draft recipients.
    hr_review_to: list[str] = Field(default_factory=list, max_length=20)
    hr_review_cc: list[str] = Field(default_factory=list, max_length=20)
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

    @model_validator(mode="after")
    def _derive_standard(self) -> LifecycleSettings:
        if self.allocation_matrix is not None:
            tiers = {t.name for t in self.device_tiers}
            unknown = sorted(
                {c.tier for r in self.allocation_matrix.cells for c in r if c.tier and c.tier not in tiers}
            )
            if unknown:
                raise ValueError(f"Cấu hình không có trong bảng tham chiếu: {', '.join(unknown)}.")
            catalog = {t.name: t.catalog_name for t in self.device_tiers if t.catalog_name}
            self.allocation_standard = [
                i.model_copy(update={"catalog_name": catalog.get(i.tier) or i.catalog_name})
                for i in self.allocation_matrix.standard()
            ]
        return self

    def label(self, field: str) -> str:
        return self.field_labels.get(field) or DEFAULT_LABELS[field]


def main_biostar(settings: LifecycleSettings) -> dict[str, Any]:
    """open_connection() arguments for the main BioStar: the chosen profile, or on "Tự động" the first enabled
    Production one that is not an extra read-only source (jobs must never write to an old office server)."""
    if settings.biostar_profile_id:
        return {"profile_id": settings.biostar_profile_id}
    return {"exclude": [s.profile_id for s in settings.biostar_sources]}


async def load(session: AsyncSession) -> LifecycleSettings:
    return await load_settings(session, MODULE_ID, SETTINGS_KEY, LifecycleSettings)
