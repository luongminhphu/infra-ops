"""Build the default stocktake record template (Biên bản kiểm kê tài sản CNTT) as a docxtpl DOCX.

Usage:
    uv run python scripts/build_stocktake_template.py app/modules/lifecycle/templates/ITD-FRM-AST-003-stocktake.docx

Layout lives in the DOCX: admins edit it in Word and upload it as a new template version (Mẫu biên bản).
Two parties sign: IT and Kế toán (Finance).
"""

from __future__ import annotations

import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


def para(doc, text: str = "", *, bold: bool = False, size: float = 11, align=None, space_after: float = 4):  # type: ignore[no-untyped-def]
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    return p


def set_cell(cell, text: str, *, bold: bool = False, align=None) -> None:  # type: ignore[no-untyped-def]
    cell.text = ""
    p = cell.paragraphs[0]
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(10)
    if align is not None:
        p.alignment = align


def shade(cell, fill: str = "E3F0F1") -> None:  # type: ignore[no-untyped-def]
    tc = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc.append(shd)


def loop_table(doc, headers: list[str], loop: str, cells: list[str], widths: list[float]):  # type: ignore[no-untyped-def]
    """Header row + one repeating row ({%tr for … %} … {%tr endfor %})."""
    t = doc.add_table(rows=4, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(headers):
        set_cell(t.rows[0].cells[i], h, bold=True)
        shade(t.rows[0].cells[i])
    set_cell(t.rows[1].cells[0], "{%tr " + loop + " %}")
    for i, c in enumerate(cells):
        set_cell(t.rows[2].cells[i], c)
    set_cell(t.rows[3].cells[0], "{%tr endfor %}")
    for row in t.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)
    return t


def build(path: str) -> None:
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(1.8)
    sec.top_margin = sec.bottom_margin = Cm(1.5)
    style = doc.styles["Normal"]
    style.font.name = "Aptos"
    style.font.size = Pt(11)

    para(doc, "{{ logo }}", space_after=6)  # company logo (Hệ thống › Logo báo cáo); empty when none is set
    para(
        doc,
        "BIÊN BẢN KIỂM KÊ TÀI SẢN CÔNG NGHỆ THÔNG TIN",
        bold=True,
        size=14,
        align=WD_ALIGN_PARAGRAPH.CENTER,
    )
    para(doc, "Số: {{ number }}", align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    para(doc, "Kỳ kiểm kê: {{ title }}")
    para(doc, "Ngày chốt sổ: {{ cutoff }}      Ngày lập biên bản: {{ date }}")
    para(
        doc,
        "Sổ sách đối chiếu: danh sách tài sản CNTT trên ServiceDesk Plus, dữ liệu lúc {{ synced_at }}.",
        space_after=10,
    )

    para(doc, "I. THÀNH PHẦN", bold=True)
    para(doc, "Đại diện IT: {{ it_rep }}{% if it_title %} – {{ it_title }}{% endif %}")
    para(
        doc,
        "Đại diện Kế toán: {{ finance_rep }}{% if finance_title %} – {{ finance_title }}{% endif %}",
        space_after=10,
    )

    para(doc, "II. KẾT QUẢ TỔNG HỢP", bold=True)
    t = doc.add_table(rows=6, cols=2)
    t.style = "Table Grid"
    rows = [
        ("Tài sản trên sổ SDP", "{{ totals.book }}"),
        ("Có trên thực tế", "{{ totals.found }}"),
        ("Không tìm thấy", "{{ totals.missing }}"),
        ("Có trên thực tế, không có trên SDP (ngoài sổ)", "{{ totals.extra }}"),
        ("Hư hỏng", "{{ totals.damaged }}"),
        ("Tổng số chênh lệch", "{{ totals.differences }}"),
    ]
    for r, (k, v) in zip(t.rows, rows, strict=True):
        set_cell(r.cells[0], k)
        set_cell(r.cells[1], v, align=WD_ALIGN_PARAGRAPH.RIGHT)
        r.cells[0].width, r.cells[1].width = Cm(12), Cm(4)
    para(doc, space_after=6)

    para(doc, "III. KẾT QUẢ THEO KHU", bold=True)
    loop_table(
        doc,
        ["Khu", "Người kiểm", "Trên sổ", "Có", "Không tìm thấy", "Ngoài sổ"],
        "for z in zones",
        [
            "{{ z.name }}",
            "{{ z.assignee }}",
            "{{ z.book }}",
            "{{ z.found }}",
            "{{ z.missing }}",
            "{{ z.extra }}",
        ],
        [3.5, 4.5, 2, 2, 2.5, 2],
    )
    para(doc, space_after=6)

    para(doc, "IV. CHÊNH LỆCH VÀ HƯỚNG XỬ LÝ", bold=True)
    para(doc, "{% if not differences %}Không có chênh lệch giữa sổ sách và thực tế.{% endif %}")
    loop_table(
        doc,
        ["STT", "Tài sản", "Asset tag / Serial", "Mã TSCĐ", "Người dùng / Khu", "Chênh lệch", "Hướng xử lý"],
        "for d in differences",
        [
            "{{ d.no }}",
            "{{ d.name }}",
            "{{ d.codes }}",
            "{{ d.fa_code }}",
            "{{ d.where }}",
            "{{ d.difference }}",
            "{{ d.resolution }}",
        ],
        [1, 3.2, 3, 2, 3, 2.8, 3],
    )
    para(doc, space_after=6)

    para(doc, "V. KẾT LUẬN", bold=True)
    para(doc, "{{ conclusion }}", space_after=10)
    closing = para(
        doc, "Biên bản được lập thành 02 bản, mỗi bên giữ 01 bản, có giá trị như nhau.", space_after=14
    )
    closing.paragraph_format.keep_with_next = True

    sig = doc.add_table(rows=3, cols=2)
    sig.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (head, name) in enumerate(
        [("ĐẠI DIỆN IT", "{{ it_rep }}"), ("ĐẠI DIỆN KẾ TOÁN", "{{ finance_rep }}")]
    ):
        set_cell(sig.rows[0].cells[i], head, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(sig.rows[1].cells[i], "(Ký, ghi rõ họ tên)\n\n\n\n", align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(sig.rows[2].cells[i], name, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    # The signature block never splits across pages: rows cannot break and each row keeps with the next.
    for n, row in enumerate(sig.rows):
        tr_pr = row._tr.get_or_add_trPr()
        cant = OxmlElement("w:cantSplit")
        tr_pr.append(cant)
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.keep_with_next = n < len(sig.rows) - 1
                p.paragraph_format.keep_together = True

    # Phụ lục: every asset of the stocktake, on its own page(s).
    doc.add_page_break()
    para(doc, "PHỤ LỤC: DANH SÁCH TÀI SẢN CHI TIẾT", bold=True, size=12, align=WD_ALIGN_PARAGRAPH.CENTER)
    para(
        doc, "Kèm theo biên bản số {{ number }} – {{ title }}", align=WD_ALIGN_PARAGRAPH.CENTER, space_after=8
    )
    loop_table(
        doc,
        [
            "STT",
            "Khu",
            "Tài sản",
            "Loại",
            "Asset tag / Serial",
            "Mã TSCĐ",
            "Người dùng",
            "Kết quả",
            "Tình trạng",
        ],
        "for a in assets",
        [
            "{{ a.no }}",
            "{{ a.zone }}",
            "{{ a.name }}",
            "{{ a.type }}",
            "{{ a.codes }}",
            "{{ a.fa_code }}",
            "{{ a.user }}",
            "{{ a.result }}",
            "{{ a.condition }}",
        ],
        [1, 1.8, 2.8, 1.6, 2.8, 1.8, 2.6, 1.8, 1.6],
    )
    doc.save(path)


if __name__ == "__main__":
    build(sys.argv[1])
