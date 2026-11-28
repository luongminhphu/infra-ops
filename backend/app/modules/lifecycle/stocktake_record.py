"""Biên bản kiểm kê tài sản CNTT (KK): the closed result of a stocktake, signed by IT and Kế toán (Finance).

Template: ``templates/ITD-FRM-AST-003-stocktake.docx`` built by ``scripts/build_stocktake_template.py``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from app.core.modules.manifest import DocumentKind, PlaceholderDoc
from app.core.timeutil import fmt_date
from app.modules.lifecycle.documents import TEMPLATES


class Totals(BaseModel):
    book: int = Field(ge=0)
    found: int = Field(ge=0)
    missing: int = Field(ge=0)
    extra: int = Field(ge=0)
    damaged: int = Field(ge=0)  # broken (rules_version 2) or broken + light damage (rules_version 1)
    damaged_light: int = Field(0, ge=0)
    differences: int = Field(ge=0)
    # Accounting register counts (defaults keep older records valid).
    tscd_matched: int = Field(0, ge=0)
    tscd_missing: int = Field(0, ge=0)
    tscd_partial: int = Field(0, ge=0)
    ccdc_matched: int = Field(0, ge=0)
    ccdc_missing: int = Field(0, ge=0)
    ccdc_partial: int = Field(0, ge=0)
    off_register: int = Field(0, ge=0)


class ZoneLine(BaseModel):
    name: str = Field(max_length=100)
    assignee: str = Field("", max_length=256)
    book: int = Field(ge=0)
    found: int = Field(ge=0)
    missing: int = Field(ge=0)
    extra: int = Field(ge=0)


class DifferenceLine(BaseModel):
    name: str = Field(max_length=256)
    codes: str = Field("", max_length=300)
    fa_code: str = Field("", max_length=128)
    where: str = Field("", max_length=400)
    difference: str = Field(max_length=200)
    resolution: str = Field("", max_length=1100)


class AssetLine(BaseModel):
    zone: str = Field("", max_length=100)
    name: str = Field(max_length=256)
    type: str = Field("", max_length=128)
    codes: str = Field("", max_length=300)
    fa_code: str = Field("", max_length=128)
    user: str = Field("", max_length=300)
    result: str = Field(max_length=40)
    condition: str = Field("", max_length=40)


class StocktakeRecordData(BaseModel):
    stocktake_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    cutoff: dt.date
    date: dt.date
    synced_at: str = Field("", max_length=40)
    totals: Totals
    zones: list[ZoneLine] = Field(default_factory=list, max_length=200)
    differences: list[DifferenceLine] = Field(default_factory=list, max_length=5000)
    # Phụ lục: every asset of the stocktake.
    assets: list[AssetLine] = Field(default_factory=list, max_length=20000)
    it_rep: str = Field(min_length=1, max_length=200)
    it_title: str = Field("", max_length=200)
    finance_rep: str = Field(min_length=1, max_length=200)
    finance_title: str = Field("", max_length=200)
    conclusion: str = Field("", max_length=2000)


def stocktake_context(data: StocktakeRecordData, number: str) -> dict[str, Any]:
    return {
        "number": number,
        "title": data.title,
        "cutoff": fmt_date(data.cutoff),
        "date": fmt_date(data.date),
        "synced_at": data.synced_at,
        "totals": data.totals.model_dump(),
        "zones": [z.model_dump() for z in data.zones],
        "differences": [{"no": n, **d.model_dump()} for n, d in enumerate(data.differences, 1)],
        "assets": [{"no": n, **a.model_dump()} for n, a in enumerate(data.assets, 1)],
        "it_rep": data.it_rep,
        "it_title": data.it_title,
        "finance_rep": data.finance_rep,
        "finance_title": data.finance_title,
        "conclusion": data.conclusion
        or (
            "Số liệu thực tế khớp với sổ sách."
            if not data.differences
            else f"Có {len(data.differences)} chênh lệch; hướng xử lý như mục IV đã được hai bên thống nhất."
        ),
    }


def stocktake_subject(data: StocktakeRecordData) -> tuple[str | None, str, str | None]:
    return None, data.title, data.stocktake_id


STOCKTAKE_RECORD = DocumentKind(
    key="lifecycle.stocktake",
    title="Biên bản kiểm kê tài sản CNTT",
    prefix="KK",
    default_template=TEMPLATES / "ITD-FRM-AST-003-stocktake.docx",
    data_model=StocktakeRecordData,
    build_context=stocktake_context,
    subject=stocktake_subject,
    placeholders=[
        PlaceholderDoc("number", "Số biên bản, cấp khi chốt", "KK-202612-0001"),
        PlaceholderDoc("title", "Tên kỳ kiểm kê", "Kiểm kê tài sản IT 2026"),
        PlaceholderDoc("cutoff", "Ngày chốt sổ (dd/MM/yyyy)", "31/12/2026"),
        PlaceholderDoc("date", "Ngày lập biên bản (dd/MM/yyyy)", "05/01/2027"),
        PlaceholderDoc("synced_at", "Thời điểm dữ liệu SDP được chốt", "31/12/2026"),
        PlaceholderDoc(
            "totals.book / found / missing / extra / damaged / damaged_light / differences",
            "Số liệu tổng hợp",
            "412",
        ),
        PlaceholderDoc(
            "totals.tscd_* / ccdc_* (matched / missing / partial) / off_register",
            "Đối chiếu sổ kế toán: chỉ số lượng dòng, không có tiền",
            "35",
        ),
        PlaceholderDoc(
            "zones[i].name / assignee / book / found / missing / extra", "Kết quả theo khu", "Tầng 5"
        ),
        PlaceholderDoc(
            "differences[i].no / name / codes / fa_code / where / difference / resolution",
            "Từng chênh lệch và hướng xử lý",
            "Không tìm thấy khi kiểm",
        ),
        PlaceholderDoc("it_rep / it_title", "Đại diện IT và chức danh", "Lê Minh Tuấn – Trưởng nhóm IT"),
        PlaceholderDoc(
            "finance_rep / finance_title", "Đại diện Kế toán và chức danh", "Phạm Thu Hà – Kế toán TSCĐ"
        ),
        PlaceholderDoc("conclusion", "Kết luận (tự điền nếu để trống)", "Số liệu thực tế khớp với sổ sách."),
        PlaceholderDoc(
            "assets[i].no / zone / name / type / codes / fa_code / user / result / condition",
            "Phụ lục: từng tài sản của kỳ kiểm kê",
            "Tầng 5 · HSC-LT-0123 · Có · Tốt",
        ),
    ],
    sample_data={
        "stocktake_id": "sample",
        "title": "Kiểm kê tài sản IT 2026",
        "cutoff": "2026-12-31",
        "date": "2027-01-05",
        "synced_at": "31/12/2026",
        "totals": {"book": 412, "found": 405, "missing": 7, "extra": 2, "damaged": 3, "differences": 12},
        "zones": [
            {"name": "Tầng 5", "assignee": "Lê Minh", "book": 120, "found": 118, "missing": 2, "extra": 1},
            {"name": "Kho IT", "assignee": "Phan Hoà", "book": 35, "found": 35, "missing": 0, "extra": 0},
        ],
        "differences": [
            {
                "name": "HSC-LT-0123",
                "codes": "LT0123 / 5CG2301XYZ",
                "fa_code": "TSCD-24-01",
                "where": "Nguyễn Văn An · Tầng 5",
                "difference": "Không tìm thấy khi kiểm",
                "resolution": "Tiếp tục tìm / làm rõ – hỏi P.Kế toán",
            },
        ],
        "it_rep": "Lê Minh Tuấn",
        "it_title": "Trưởng nhóm IT",
        "finance_rep": "Phạm Thu Hà",
        "finance_title": "Kế toán TSCĐ",
        "conclusion": "",
        "assets": [
            {
                "zone": "Tầng 5",
                "name": "HSC-LT-0123",
                "type": "Laptop",
                "codes": "LT0123 / 5CG2301XYZ",
                "fa_code": "TSCD-24-01",
                "user": "Nguyễn Văn An (003551)",
                "result": "Không tìm thấy",
                "condition": "",
            },
            {
                "zone": "Kho IT",
                "name": "HSC-MN-0001",
                "type": "Monitor",
                "codes": "MN0001",
                "fa_code": "",
                "user": "",
                "result": "Có",
                "condition": "Tốt",
            },
        ],
    },
    view_permission="lifecycle.stocktake.view_all",  # the record lists every zone: not for Operators
    create_permission="lifecycle.stocktake.manage",
    manage_permission="lifecycle.stocktake.manage",
)
