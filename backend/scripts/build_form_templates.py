"""Convert the company's handover/return Word forms into docxtpl templates.

Usage:
    uv run python scripts/build_form_templates.py handover ITD-FRM-AST-001.docx out/handover.docx
    uv run python scripts/build_form_templates.py return   ITD-FRM-AST-002.docx out/return.docx

The script keeps every style/layout of the original and only:
- replaces the blank lines (____) with Jinja placeholders, in document order;
- turns the fixed 4-row asset table into one repeating row ({%tr for %});
- adds the record number under the header title;
- (return form) adds a "Tình trạng / Condition" column;
- replaces the ☐ boxes of Section B with placeholders the app fills with ☑ / ☐.
Admins can later edit the produced template in Word and upload it as a new version.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table

BLANK = re.compile(r"_{4,}(?:/_{2,}/_{4,})?|_{2,}/_{2,}/_{4,}")


def all_paragraphs(doc):  # type: ignore[no-untyped-def]
    """Body paragraphs in document order, descending into tables."""
    yield from doc.element.body.iter(qn("w:p"))


def set_text(r_el, text: str) -> None:
    ts = r_el.findall(qn("w:t"))
    ts[0].text = text
    ts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for extra in ts[1:]:
        r_el.remove(extra)


def run_text(r_el) -> str:
    return "".join(t.text or "" for t in r_el.findall(qn("w:t")))


def fill_blanks(doc, values: list[str | None]) -> None:  # type: ignore[no-untyped-def]
    """Replace each blank (outside the asset table) with the next value."""
    queue = list(values)
    for p in all_paragraphs(doc):
        if p.getparent() is not None and _in_asset_table(p):
            continue
        for r in p.iter(qn("w:r")):
            text = run_text(r)
            if not BLANK.search(text):
                continue
            # None keeps the original blank (signature lines stay blank for handwriting).
            new = BLANK.sub(
                lambda m: v if (v := queue.pop(0) if queue else None) is not None else m.group(0), text
            )
            set_text(r, new)
    if queue:
        raise SystemExit(f"Not all placeholders used: {queue}")


_asset_tbl = None


def _in_asset_table(el) -> bool:  # type: ignore[no-untyped-def]
    while el is not None:
        if el is _asset_tbl:
            return True
        el = el.getparent()
    return False


def find_table(doc, header_text: str) -> Table:  # type: ignore[no-untyped-def]
    for t in doc.element.body.iter(qn("w:tbl")):
        text = "".join(x.text or "" for x in t.iter(qn("w:t")))
        if header_text in text and "No." in text:
            # innermost table containing the header
            inner = [
                c
                for c in t.iter(qn("w:tbl"))
                if c is not t and header_text in "".join(x.text or "" for x in c.iter(qn("w:t")))
            ]
            return Table(inner[-1] if inner else t, doc)
    raise SystemExit(f"Table with {header_text!r} not found")


def cell_set(cell, text: str) -> None:  # type: ignore[no-untyped-def]
    """Put text into the cell's first run (keeping its formatting) and clear the rest."""
    runs = list(cell._tc.iter(qn("w:r")))
    if not runs:
        cell.paragraphs[0].add_run(text)
        return
    set_text(runs[0], text) if runs[0].findall(qn("w:t")) else runs[0].append(_t(text))
    for r in runs[1:]:
        r.getparent().remove(r)


def _t(text: str):  # type: ignore[no-untyped-def]
    from docx.oxml import OxmlElement

    t = OxmlElement("w:t")
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return t


def asset_loop(table: Table, cells: list[str], add_condition: bool) -> None:
    rows = table.rows
    header_idx = next(i for i, r in enumerate(rows) if "No." in "".join(c.text for c in r.cells))
    data_rows = rows[header_idx + 1 :]
    if add_condition:
        _add_condition_column(table)
        rows = table.rows
        data_rows = rows[header_idx + 1 :]
    first, item, last = data_rows[0], data_rows[1], data_rows[2]
    cell_set(first.cells[0], "{%tr for a in assets %}")
    for c in first.cells[1:]:
        cell_set(c, "")
    for c, value in zip(item.cells, cells, strict=True):
        cell_set(c, value)
    cell_set(last.cells[0], "{%tr endfor %}")
    for c in last.cells[1:]:
        cell_set(c, "")
    for extra in data_rows[3:]:
        extra._tr.getparent().remove(extra._tr)


def _add_condition_column(table: Table) -> None:
    """Split the Notes column: insert "Tình trạng / Condition" before it."""
    grid = table._tbl.find(qn("w:tblGrid"))
    cols = grid.findall(qn("w:gridCol"))
    notes = cols[-1]
    w = int(notes.get(qn("w:w")))
    notes.set(qn("w:w"), str(w // 2))
    new_col = copy.deepcopy(notes)
    grid.insert(list(grid).index(notes), new_col)
    for row in table.rows:
        tcs = row._tr.findall(qn("w:tc"))
        last = tcs[-1]
        clone = copy.deepcopy(last)
        for tc in (last, clone):
            tcw = tc.find(qn("w:tcPr")).find(qn("w:tcW")) if tc.find(qn("w:tcPr")) is not None else None
            if tcw is not None:
                tcw.set(qn("w:w"), str(int(tcw.get(qn("w:w"))) // 2))
        last.addprevious(clone)
        texts = list(clone.iter(qn("w:t")))
        joined = "".join(t.text or "" for t in texts)
        if "Ghi chú" in joined:
            # header row: "Ghi chú" / "Notes" → "Tình trạng" / "Condition"
            for t in texts:
                t.text = (t.text or "").replace("Ghi chú", "Tình trạng").replace("Notes", "Condition")


def header_number(doc, title_marker: str) -> None:  # type: ignore[no-untyped-def]
    for section in doc.sections:
        for hdr in (section.header, section.first_page_header, section.even_page_header):
            for p in hdr._element.iter(qn("w:p")):
                text = "".join(t.text or "" for t in p.iter(qn("w:t")))
                if text.strip() == title_marker:
                    clone = copy.deepcopy(p)
                    runs = clone.findall(qn("w:r"))
                    set_text(runs[0], "Số / No.: {{ number }}")
                    for r in runs[1:]:
                        clone.remove(r)
                    p.addnext(clone)
                    return
    raise SystemExit("Header title paragraph not found")


SYMBOL_FONT = "Segoe UI Symbol"


def _symbol_font(r_el) -> None:  # type: ignore[no-untyped-def]
    """Pin checkbox runs to a symbol font (Word has it; the container aliases it to DejaVu Sans)."""
    from docx.oxml import OxmlElement

    rpr = r_el.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        r_el.insert(0, rpr)
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attr), SYMBOL_FONT)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme", "w:hint"):
        fonts.attrib.pop(qn(attr), None)


def fixed_layout(doc) -> None:  # type: ignore[no-untyped-def]
    """Fixed table layout: filled-in names must not re-flow the designed columns."""
    from docx.oxml import OxmlElement

    for tbl in doc.element.body.iter(qn("w:tbl")):
        tblpr = tbl.find(qn("w:tblPr"))
        layout = tblpr.find(qn("w:tblLayout"))
        if layout is None:
            layout = OxmlElement("w:tblLayout")
            tblpr.append(layout)
        layout.set(qn("w:type"), "fixed")
        # Never split a row across pages (keeps each sign-off block together).
        for tr in tbl.findall(qn("w:tr")):
            trpr = tr.find(qn("w:trPr"))
            if trpr is None:
                trpr = OxmlElement("w:trPr")
                tr.insert(0, trpr)
            if trpr.find(qn("w:cantSplit")) is None:
                trpr.insert(0, OxmlElement("w:cantSplit"))


def checkboxes(doc, prefix: str, start_marker: str, item_count: int, with_software: int = 0) -> None:  # type: ignore[no-untyped-def]
    """Replace ☐ in Section B: per checklist row OK/N-A, then software 'installed' boxes."""
    body = doc.element.body
    seen_start = False
    boxes = []
    for r in body.iter(qn("w:r")):
        t = run_text(r)
        if start_marker in t:
            seen_start = True
        if seen_start and t.strip() == "☐":
            boxes.append(r)
    need = item_count * 2 + with_software
    if len(boxes) < need:
        raise SystemExit(f"Expected {need} boxes, found {len(boxes)}")
    for r in boxes[:need]:
        _symbol_font(r)
    for i in range(item_count):
        set_text(boxes[2 * i], f"{{{{ {prefix}[{i}].ok }}}}")
        set_text(boxes[2 * i + 1], f"{{{{ {prefix}[{i}].na }}}}")
    for j in range(with_software):
        set_text(boxes[item_count * 2 + j], f"{{{{ software[{j}].mark }}}}")


def build(kind: str, src: Path, dst: Path) -> None:
    global _asset_tbl
    doc = Document(str(src))
    table = find_table(doc, "Asset tag")
    _asset_tbl = table._tbl
    if kind == "handover":
        header_number(doc, "IT Helpdesk / Asset Management")
        fill_blanks(
            doc,
            [
                "{{ date }}",
                "{{ employee.full_name }}",
                "{{ employee.id }}",
                "{{ employee.department }}",
                "{{ employee.job_title }}",
                "{{ employee.work_location }}",
                # Section A sign-off: IT rep name/signature/date, receiver name/signature/date
                "{{ it_rep }}",
                None,
                "{{ date }}",
                "{{ employee.full_name }}",
                None,
                "{{ date }}",
                # Section B: "Other: ____" software, then IT rep / reviewer sign-off
                "{{ software_other }}",
                "{{ it_rep }}",
                None,
                "{{ date }}",
                "{{ reviewer }}",
                None,
                "{{ date }}",
            ],
        )
        asset_loop(
            table, ["{{ loop.index }}", "{{ a.name }}", "{{ a.tag }}", "{{ a.date }}", "{{ a.note }}"], False
        )
        checkboxes(doc, "checklist", "NỘI DUNG B", 8, with_software=10)
    else:
        header_number(doc, "IT Helpdesk / Asset Management")
        fill_blanks(
            doc,
            [
                "{{ date }}",
                "{{ employee.full_name }}",
                "{{ employee.id }}",
                "{{ employee.department }}",
                "{{ employee.job_title }}",
                "{{ employee.work_location }}",
                "{{ it_rep }}",
                None,
                "{{ date }}",
                "{{ employee.full_name }}",
                None,
                "{{ date }}",
                "{{ it_rep }}",
                None,
                "{{ date }}",
                "{{ reviewer }}",
                None,
                "{{ date }}",
            ],
        )
        asset_loop(
            table,
            [
                "{{ loop.index }}",
                "{{ a.name }}",
                "{{ a.tag }}",
                "{{ a.date }}",
                "{{ a.condition }}",
                "{{ a.note }}",
            ],
            True,
        )
        checkboxes(doc, "checklist", "NỘI DUNG B", 3)
    fixed_layout(doc)
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dst))
    print(f"wrote {dst}")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("handover", "return"):
        raise SystemExit(__doc__)
    build(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
