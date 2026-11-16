"""Template rendering (docxtpl) and PDF conversion (LibreOffice headless)."""

from __future__ import annotations

import asyncio
import io
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from docxtpl import DocxTemplate
from jinja2 import TemplateError

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)
_pdf_slots = asyncio.Semaphore(2)


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return "word/document.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def template_variables(data: bytes) -> set[str]:
    """Top-level Jinja variables used by a DOCX template; raises a readable error on bad syntax."""
    if not _is_docx(data):
        raise AppError("File không phải tài liệu Word (.docx).", code="not_docx")
    try:
        return set(DocxTemplate(io.BytesIO(data)).get_undeclared_template_variables())
    except TemplateError as exc:
        raise AppError(f"Mẫu có lỗi cú pháp placeholder: {exc}", code="template_syntax") from exc


def render_docx(template: bytes, context: dict[str, Any]) -> bytes:
    doc = DocxTemplate(io.BytesIO(template))
    try:
        # autoescape: user-entered text (names, notes) must not break the XML.
        doc.render(context, autoescape=True)
    except TemplateError as exc:
        raise AppError(f"Không điền được mẫu biên bản: {exc}", code="template_render") from exc
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


async def docx_to_pdf(data: bytes) -> bytes:
    """Convert with an isolated LibreOffice profile per call so conversions can run concurrently."""
    settings = get_settings()
    if shutil.which(settings.soffice_path) is None:
        raise AppError("Máy chủ chưa cài LibreOffice để xuất PDF.", code="pdf_unavailable")
    async with _pdf_slots:
        with tempfile.TemporaryDirectory(prefix="infraops-pdf-") as tmp:
            src = Path(tmp) / "document.docx"
            src.write_bytes(data)
            profile = Path(tmp) / f"lo-{uuid.uuid4().hex}"
            proc = await asyncio.create_subprocess_exec(
                settings.soffice_path,
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                "pdf",
                "--outdir",
                tmp,
                str(src),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=settings.pdf_timeout_seconds)
            except TimeoutError as exc:
                proc.kill()
                raise AppError("Xuất PDF quá thời gian. Thử lại sau.", code="pdf_timeout") from exc
            pdf = Path(tmp) / "document.pdf"
            if proc.returncode != 0 or not pdf.exists():
                log.error(
                    "pdf_conversion_failed", code=proc.returncode, stderr=stderr.decode(errors="ignore")[:500]
                )
                raise AppError("Không xuất được PDF từ mẫu biên bản.", code="pdf_failed")
            return pdf.read_bytes()
