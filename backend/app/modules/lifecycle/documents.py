"""Biên bản Bàn giao (BG) / Thu hồi (TH) – data models and template context.

Templates: ``templates/ITD-FRM-AST-001-handover.docx`` and ``ITD-FRM-AST-002-return.docx`` built
from the company forms with ``scripts/build_form_templates.py``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.modules.manifest import DocumentKind, PlaceholderDoc

TEMPLATES = Path(__file__).parent / "templates"

HANDOVER_CHECKLIST = [
    "Xóa dữ liệu cũ hoặc xác nhận thiết bị mới trước khi cấp phát",
    "Cài hệ điều hành chuẩn doanh nghiệp và cập nhật bản vá mới nhất",
    "Kích hoạt bản quyền hệ điều hành và ứng dụng được cấp phép",
    "Đặt tên máy theo chuẩn, join domain hoặc Entra ID theo chính sách",
    "Cài Endpoint Protection/antivirus, bật tường lửa và xác nhận trạng thái bảo vệ",
    "Cấu hình tài khoản người dùng, quyền truy cập và local admin theo chuẩn",
    "Kết nối mạng nội bộ/Wi-Fi doanh nghiệp và kiểm tra truy cập tài nguyên",
    "Cập nhật thông tin thiết bị trên ITSM/CMDB/Asset register",
]
HANDOVER_SOFTWARE = [
    "Microsoft 365 Apps / Office productivity suite",
    "Corporate Web Browser",
    "Sophos Endpoint Agent",
    "IT Asset Management Agent",
    "Remote Support Tool",
    "PDF Reader",
    "7-Zip or approved archive utility",
    "Vietnamese typing utility / UniKey",
    "Line-of-business applications (if applicable)",
    "Other",
]
RETURN_CHECKLIST = [
    "Tiếp nhận thiết bị đúng mã tài sản/serial và đối chiếu với hồ sơ quản lý",
    "Xóa dữ liệu cũ an toàn theo quy trình/policy trước khi tái sử dụng, lưu kho hoặc thanh lý",
    "Cập nhật trạng thái thiết bị trên ITSM/CMDB/Asset register sau khi thu hồi",
]
CONDITION_LABEL = {
    "good": "Tốt",
    "damaged": "Hỏng",
    "missing_accessory": "Thiếu phụ kiện",
    "not_returned": "Chưa thu hồi",
}

Check = Literal["ok", "na", ""]
Condition = Literal["good", "damaged", "missing_accessory", "not_returned"]


class Employee(BaseModel):
    id: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z]+$")
    full_name: str = Field(min_length=1, max_length=200)
    department: str = Field("", max_length=200)
    job_title: str = Field("", max_length=200)
    work_location: str = Field("", max_length=200)


class AssetLine(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tag: str = Field("", max_length=100)
    date: dt.date | None = None
    note: str = Field("", max_length=300)
    sdp_asset_id: str | None = Field(None, max_length=32)


class ReturnAssetLine(AssetLine):
    condition: Condition = "good"


class _Common(BaseModel):
    employee: Employee
    date: dt.date
    it_rep: str = Field(min_length=1, max_length=200)
    reviewer: str = Field("", max_length=200)
    request_id: str | None = Field(None, max_length=32)


class HandoverData(_Common):
    assets: list[AssetLine] = Field(min_length=1, max_length=50)
    checklist: list[Check] = Field(default_factory=lambda: [""] * len(HANDOVER_CHECKLIST))
    software: list[bool] = Field(default_factory=lambda: [False] * len(HANDOVER_SOFTWARE))
    software_other: str = Field("", max_length=200)

    @field_validator("checklist")
    @classmethod
    def _cl(cls, v: list[str]) -> list[str]:
        return (v + [""] * len(HANDOVER_CHECKLIST))[: len(HANDOVER_CHECKLIST)]

    @field_validator("software")
    @classmethod
    def _sw(cls, v: list[bool]) -> list[bool]:
        return (v + [False] * len(HANDOVER_SOFTWARE))[: len(HANDOVER_SOFTWARE)]


class ReturnData(_Common):
    assets: list[ReturnAssetLine] = Field(min_length=1, max_length=50)
    checklist: list[Check] = Field(default_factory=lambda: [""] * len(RETURN_CHECKLIST))

    @field_validator("checklist")
    @classmethod
    def _cl(cls, v: list[str]) -> list[str]:
        return (v + [""] * len(RETURN_CHECKLIST))[: len(RETURN_CHECKLIST)]


def _d(value: dt.date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def _box(on: bool) -> str:
    return "☑" if on else "☐"


def _common_context(data: _Common, number: str) -> dict[str, Any]:
    return {
        "number": number,
        "date": _d(data.date),
        "it_rep": data.it_rep,
        "reviewer": data.reviewer,
        "employee": data.employee.model_dump(),
        "checklist": [{"ok": _box(c == "ok"), "na": _box(c == "na")} for c in data.checklist],  # type: ignore[attr-defined]
    }


def handover_context(data: HandoverData, number: str) -> dict[str, Any]:
    return {
        **_common_context(data, number),
        "assets": [{"name": a.name, "tag": a.tag, "date": _d(a.date), "note": a.note} for a in data.assets],
        "software": [{"mark": _box(b)} for b in data.software],
        "software_other": data.software_other,
    }


def return_context(data: ReturnData, number: str) -> dict[str, Any]:
    return {
        **_common_context(data, number),
        "assets": [
            {
                "name": a.name,
                "tag": a.tag,
                "date": _d(a.date if a.condition != "not_returned" else None),
                "condition": CONDITION_LABEL[a.condition],
                "note": a.note,
            }
            for a in data.assets
        ],
    }


def subject(data: _Common) -> tuple[str | None, str, str | None]:
    return data.employee.id, f"{data.employee.full_name} ({data.employee.id})", data.request_id


COMMON_PLACEHOLDERS = [
    PlaceholderDoc("number", "Số biên bản, cấp khi chốt", "BG-202611-0001"),
    PlaceholderDoc("date", "Ngày bàn giao / thu hồi (dd/MM/yyyy)", "16/11/2026"),
    PlaceholderDoc("employee.full_name", "Họ tên nhân viên", "Nguyễn Văn An"),
    PlaceholderDoc("employee.id", "Mã nhân viên", "003551"),
    PlaceholderDoc("employee.department", "Phòng ban", "Kinh Doanh"),
    PlaceholderDoc("employee.job_title", "Chức danh", "Chuyên viên tư vấn"),
    PlaceholderDoc("employee.work_location", "Địa điểm làm việc", "Chi nhánh 1"),
    PlaceholderDoc("it_rep", "Họ tên đại diện IT", "Lê Minh Tuấn"),
    PlaceholderDoc("reviewer", "Người kiểm tra (Nội dung B)", "Trần Văn Quản"),
    PlaceholderDoc(
        "assets", "Danh sách tài sản – dùng trong dòng lặp {%tr for a in assets %} … {%tr endfor %}"
    ),
    PlaceholderDoc("loop.index", "Số thứ tự trong dòng lặp", "1"),
    PlaceholderDoc("a.name", "Tên tài sản trên SDP", "HSC-LT-0123"),
    PlaceholderDoc("a.tag", "Asset tag", "LT0123"),
    PlaceholderDoc("a.date", "Ngày cấp / ngày thu hồi", "16/11/2026"),
    PlaceholderDoc("a.note", "Ghi chú", "Kèm sạc"),
    PlaceholderDoc("checklist[i].ok", "Ô OK của mục kiểm tra thứ i (bắt đầu từ 0): ☑ hoặc ☐", "☑"),
    PlaceholderDoc("checklist[i].na", "Ô N/A của mục kiểm tra thứ i: ☑ hoặc ☐", "☐"),
]

_SAMPLE_COMMON: dict[str, Any] = {
    "employee": {
        "id": "003551",
        "full_name": "Nguyễn Văn An",
        "department": "Kinh Doanh",
        "job_title": "Chuyên viên tư vấn",
        "work_location": "Chi nhánh 1",
    },
    "date": "2026-11-16",
    "it_rep": "Lê Minh Tuấn",
    "reviewer": "Trần Văn Quản",
}

HANDOVER = DocumentKind(
    key="lifecycle.handover",
    title="Biên bản Bàn giao thiết bị CNTT",
    prefix="BG",
    default_template=TEMPLATES / "ITD-FRM-AST-001-handover.docx",
    data_model=HandoverData,
    build_context=handover_context,
    subject=subject,
    placeholders=[
        *COMMON_PLACEHOLDERS,
        PlaceholderDoc("software[i].mark", "Ô “Đã cài” của phần mềm thứ i: ☑ hoặc ☐", "☑"),
        PlaceholderDoc("software_other", "Tên phần mềm khác", "Zalo PC"),
    ],
    sample_data={
        **_SAMPLE_COMMON,
        "assets": [
            {"name": "HSC-LT-0123", "tag": "LT0123", "date": "2026-11-16", "note": "Kèm sạc"},
            {"name": "HSC-MN-0042", "tag": "MN0042", "date": "2026-11-16"},
        ],
        "checklist": ["ok"] * 7 + ["na"],
        "software": [True] * 8 + [False, False],
    },
    view_permission="lifecycle.documents.view",
    create_permission="lifecycle.documents.create",
    manage_permission="lifecycle.templates.manage",
)

RETURN = DocumentKind(
    key="lifecycle.return",
    title="Biên bản Thu hồi thiết bị CNTT",
    prefix="TH",
    default_template=TEMPLATES / "ITD-FRM-AST-002-return.docx",
    data_model=ReturnData,
    build_context=return_context,
    subject=subject,
    placeholders=[
        *COMMON_PLACEHOLDERS,
        PlaceholderDoc("a.condition", "Tình trạng: Tốt / Hỏng / Thiếu phụ kiện / Chưa thu hồi", "Tốt"),
    ],
    sample_data={
        **_SAMPLE_COMMON,
        "assets": [
            {"name": "HSC-LT-0123", "tag": "LT0123", "date": "2026-11-16", "condition": "good"},
            {
                "name": "HSC-MS-0007",
                "tag": "MS0007",
                "condition": "not_returned",
                "note": "Nhân viên báo mất",
            },
        ],
        "checklist": ["ok", "ok", "na"],
    },
    view_permission="lifecycle.documents.view",
    create_permission="lifecycle.documents.create",
    manage_permission="lifecycle.templates.manage",
)

KIND_BY_SHORT = {"handover": HANDOVER, "return": RETURN}
