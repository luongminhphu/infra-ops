"""Serial numbers read from a vendor handover document (pure parsing, no database)."""

from app.modules.workload.handover import rows_from_text


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
