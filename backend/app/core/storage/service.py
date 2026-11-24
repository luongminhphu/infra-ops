"""File storage behind a small interface (local directory / PVC now; S3/MinIO later)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.storage.models import StoredFile

_MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    # DOCX is a ZIP container
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
}


def sniff(data: bytes, allowed: set[str]) -> str:
    """Return the MIME type detected from magic bytes, or raise when it is not allowed."""
    for mime in allowed:
        if any(data.startswith(m) for m in _MAGIC.get(mime, ())):
            return mime
    raise AppError(
        "Định dạng file không được hỗ trợ.", code="unsupported_file", details={"allowed": sorted(allowed)}
    )


def check_size(data: bytes) -> None:
    limit = get_settings().max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise AppError(f"File quá lớn (tối đa {get_settings().max_upload_mb} MB).", code="file_too_large")
    if not data:
        raise AppError("File rỗng.", code="empty_file")


def safe_filename(name: str) -> str:
    base = os.path.basename(name or "file").replace("\x00", "")
    return ("".join(c for c in base if c.isprintable() and c not in '\\/:*?"<>|') or "file")[:200]


class LocalFileStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise NotFound("File không tồn tại.")
        return path

    def _write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, key, data)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise NotFound("Không tìm thấy nội dung file trên kho lưu trữ.") from exc


def get_storage() -> LocalFileStorage:
    return LocalFileStorage(get_settings().storage_dir)


async def save_file(
    session: AsyncSession, data: bytes, filename: str, mime: str, uploaded_by: str | None
) -> StoredFile:
    file_id = uuid.uuid4()
    key = f"{file_id.hex[:2]}/{file_id.hex}"
    await get_storage().put(key, data)
    row = StoredFile(
        id=file_id,
        filename=safe_filename(filename),
        mime=mime,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=key,
        uploaded_by=uploaded_by,
    )
    session.add(row)
    await session.flush()
    return row


async def read_file(session: AsyncSession, file_id: uuid.UUID) -> tuple[StoredFile, bytes]:
    row = await session.get(StoredFile, file_id)
    if row is None:
        raise NotFound("File không tồn tại.")
    return row, await get_storage().get(row.storage_key)
