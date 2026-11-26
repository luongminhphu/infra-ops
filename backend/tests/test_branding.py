"""Company logo (Hệ thống › Logo báo cáo) printed on Excel reports and DOCX/PDF records."""

from __future__ import annotations

import io
import zipfile

from openpyxl import Workbook, load_workbook
from PIL import Image

from app.core.branding.service import check_logo, xlsx_logo
from app.core.documents.engine import render_docx
from app.modules.lifecycle.stocktake_record import STOCKTAKE_RECORD
from tests.test_workload import _user


def png(w: int = 120, h: int = 40) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (31, 91, 211)).save(buf, "PNG")
    return buf.getvalue()


def test_logo_goes_above_the_content_and_moves_the_frozen_header() -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["Mã NV", "Họ tên"])
    ws.append(["003551", "An"])
    ws.freeze_panes, ws.auto_filter.ref = "C2", "A1:B2"
    assert xlsx_logo(ws, png()) == 3
    assert (ws["A4"].value, ws.freeze_panes, ws.auto_filter.ref) == ("Mã NV", "C5", "A4:B5")
    summary = wb.create_sheet("Tổng hợp")
    summary.append(["Kỳ kiểm kê", "2026"])
    xlsx_logo(summary, png())
    assert summary["A4"].value == "Kỳ kiểm kê" and len(summary._images) == 1
    assert xlsx_logo(wb.create_sheet("x"), None) == 0


def test_only_sane_images_are_accepted() -> None:
    assert check_logo(png())[0] == "image/png"
    for bad in (b"%PDF-1.7 not an image", b"\x89PNG\r\n\x1a\nbroken", png(8, 8)):
        try:
            check_logo(bad)
        except Exception as exc:
            assert getattr(exc, "code", "") in ("unsupported_file", "logo_unreadable", "logo_size")
        else:
            raise AssertionError("accepted a bad logo")


def test_records_print_the_logo_placeholder() -> None:
    k = STOCKTAKE_RECORD
    ctx = k.build_context(k.data_model.model_validate(k.sample_data), "KK-202612-0001")
    with_logo = zipfile.ZipFile(io.BytesIO(render_docx(k.default_template.read_bytes(), ctx, png())))
    without = zipfile.ZipFile(io.BytesIO(render_docx(k.default_template.read_bytes(), ctx)))
    assert any(n.startswith("word/media/") for n in with_logo.namelist())
    assert not any(n.startswith("word/media/") for n in without.namelist())


async def test_admin_sets_the_logo_and_reports_carry_it(admin, client) -> None:  # type: ignore[no-untyped-def]
    assert (await admin.get("/api/core/branding")).json()["has_logo"] in (True, False)
    bad = await admin.post(
        "/api/core/branding/logo", files={"file": ("logo.gif", b"GIF89a....", "image/gif")}
    )
    assert bad.json()["error"]["code"] == "unsupported_file"
    ok = await admin.post("/api/core/branding/logo", files={"file": ("logo.png", png(), "image/png")})
    assert ok.status_code == 200 and ok.json()["has_logo"], ok.text
    got = await admin.get("/api/core/branding/logo")
    assert got.status_code == 200 and got.content.startswith(b"\x89PNG")
    audit = (await admin.get("/api/core/audit", params={"action": "settings.updated"})).json()
    assert audit["items"]

    # The offboarding review Excel (a Báo cáo page) now starts with the logo.
    x = await admin.get(
        "/api/lifecycle/offboard-review/export", params={"month": "2026-10", "format": "xlsx"}
    )
    assert x.status_code == 200, x.text
    ws = load_workbook(io.BytesIO(x.content)).active
    assert ws.freeze_panes == "C5" and ws["A4"].value == "STT"

    # Only Administrator by default; everyone signed in may fetch the image.
    await _user(admin, "manager", "brand-mgr@test.local")
    await client.login("brand-mgr@test.local")
    assert (await client.get("/api/core/branding/logo")).status_code == 200
    assert (await client.delete("/api/core/branding/logo")).status_code == 403
    assert (await admin.delete("/api/core/branding/logo")).json()["has_logo"] is False
