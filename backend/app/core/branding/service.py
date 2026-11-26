"""Company logo printed on exported reports (Excel) and records (DOCX/PDF, the ``{{ logo }}`` placeholder).

The image is kept with ``core.storage``; its file id lives in the core settings (``branding``), audited.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from openpyxl.drawing.image import Image as XlImage
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.utils.cell import coordinate_from_string
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor
from app.core.errors import AppError, NotFound
from app.core.settings.service import load_settings, save_settings
from app.core.storage.service import read_file, save_file, sniff

MODULE_ID, KEY = "core", "branding"
MAX_BYTES = 1024 * 1024
MAX_SIDE = 4000
LOGO_TYPES = {"image/png", "image/jpeg"}


class Branding(BaseModel):
    logo_file_id: uuid.UUID | None = None


async def load(session: AsyncSession) -> Branding:
    return await load_settings(session, MODULE_ID, KEY, Branding)


async def logo_bytes(session: AsyncSession) -> bytes | None:
    """The current logo, or None when none is set (reports then print without one)."""
    b = await load(session)
    if b.logo_file_id is None:
        return None
    try:
        _, data = await read_file(session, b.logo_file_id)
    except NotFound:
        return None
    return data


def check_logo(data: bytes) -> tuple[str, int, int]:
    """MIME type and pixel size of an uploaded logo; refuses what is not a sane PNG/JPEG image."""
    if len(data) > MAX_BYTES:
        raise AppError("Logo tối đa 1 MB.", code="logo_too_large")
    mime = sniff(data, LOGO_TYPES)
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            w, h = img.size
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise AppError("File ảnh hỏng hoặc không đọc được.", code="logo_unreadable") from exc
    if not (16 <= w <= MAX_SIDE and 16 <= h <= MAX_SIDE):
        raise AppError(f"Kích thước logo phải từ 16 đến {MAX_SIDE} px mỗi chiều.", code="logo_size")
    return mime, w, h


async def set_logo(session: AsyncSession, actor: Actor, data: bytes) -> Branding:
    mime, _, _ = check_logo(data)
    f = await save_file(session, data, f"logo.{'png' if mime == 'image/png' else 'jpg'}", mime, actor.upn)
    return await save_settings(session, actor, MODULE_ID, KEY, Branding, {"logo_file_id": str(f.id)})


async def clear_logo(session: AsyncSession, actor: Actor) -> Branding:
    return await save_settings(session, actor, MODULE_ID, KEY, Branding, {"logo_file_id": None})


def xlsx_logo(ws: Any, logo: bytes | None, height_px: int = 48, rows: int = 3) -> int:
    """Put the logo at the top-left of a written worksheet: ``rows`` rows are inserted above the content and the
    frozen header and filter move down with it. Call it after the sheet is filled.

    Returns how many rows the logo takes (0 without a logo).
    """
    if not logo:
        return 0
    with Image.open(io.BytesIO(logo)) as img:
        w, h = img.size
    ws.insert_rows(1, amount=rows)
    if ws.freeze_panes:  # keep the header row frozen where it moved to
        col, row = coordinate_from_string(ws.freeze_panes)
        ws.freeze_panes = f"{col}{row + rows}"
    if ws.auto_filter.ref:
        c1, r1, c2, r2 = range_boundaries(ws.auto_filter.ref)
        ws.auto_filter.ref = f"{get_column_letter(c1)}{r1 + rows}:{get_column_letter(c2)}{r2 + rows}"
    pic = XlImage(io.BytesIO(logo))
    pic.height, pic.width = height_px, max(1, round(w * height_px / h))
    ws.add_image(pic, "A1")
    return rows
