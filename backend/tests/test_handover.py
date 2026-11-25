"""Serial numbers read from a vendor handover document (pure parsing, no database)."""

import io

from openpyxl import Workbook

from app.modules.workload.handover import rows_from_text, rows_from_xlsx


def test_labeled_serials() -> None:
    rows = rows_from_text(
        "Laptop Dell 5440 S/N: CN0AB123\nMàn hình Dell E2423H Serial Number: 7XK9D53\nSố serial: 12345678"
    )
    assert [r["serial"] for r in rows] == ["CN0AB123", "7XK9D53", "12345678"]
    assert rows[0]["text"].startswith("Laptop Dell 5440")


def test_table_without_labels_skips_model_names() -> None:
    text = "STT Thiết bị Model Serial\n1 Laptop LAT5440 5CD1234XYZ\n2 Laptop LAT5440 5CD1234XZZ\nTổng 2"
    assert [r["serial"] for r in rows_from_text(text)] == ["5CD1234XYZ", "5CD1234XZZ"]


def test_no_serial_header() -> None:
    assert rows_from_text("Biên bản bàn giao\nLaptop Dell 5440 x 2") == []


def test_duplicates_removed() -> None:
    assert len(rows_from_text("S/N: AB12345\nSN: ab12345")) == 1


def test_xlsx_title_with_serial_is_not_the_header() -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["DANH SÁCH SERIAL THIẾT BỊ BÀN GIAO"])
    ws.append(["STT", "Thiết bị", "Serial"])
    ws.append([1, "Laptop Dell 5440", "5CD1234XYZ"])
    buf = io.BytesIO()
    wb.save(buf)
    assert [r["serial"] for r in rows_from_xlsx(buf.getvalue())] == ["5CD1234XYZ"]
