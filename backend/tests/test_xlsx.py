from openpyxl import Workbook

from app.core.xlsx import header, safe


def test_safe_neutralises_formula_prefixes() -> None:
    for v in ("=1+1", "+1", "-1", "@x", "\tx", "\rx"):
        assert safe(v) == f"'{v}"
    assert safe("plain") == "plain" and safe("") == "" and safe(5) == 5 and safe(None) is None


def test_header_bold_fill_and_widths() -> None:
    ws = Workbook().active
    assert ws is not None
    header(ws, ["A", "B"], [10, 20])
    assert ws["A1"].font.bold and ws["A1"].fill.fgColor.rgb.endswith("E3F0F1")
    assert ws.column_dimensions["A"].width == 10 and ws.column_dimensions["B"].width == 20
