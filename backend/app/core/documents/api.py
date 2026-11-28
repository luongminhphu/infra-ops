from __future__ import annotations

import uuid
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser, current_user
from app.core.db import get_session
from app.core.documents import service
from app.core.documents.models import Document, TemplateVersion
from app.core.errors import NotFound, PermissionDenied
from app.core.modules.manifest import DocumentKind
from app.core.modules.registry import get_registry
from app.core.storage.models import StoredFile
from app.core.storage.service import check_size, read_file, sniff

router = APIRouter(tags=["documents"])
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _need(user: CurrentUser, kind: DocumentKind, which: Literal["view", "create", "manage"]) -> None:
    code = {"view": kind.view_permission, "create": kind.create_permission, "manage": kind.manage_permission}[
        which
    ]
    if not user.has(code):
        raise PermissionDenied("Bạn không có quyền thực hiện thao tác này.", details={"missing": [code]})


def _download(data: bytes, filename: str, mime: str, inline: bool = False) -> Response:
    disposition = "inline" if inline else "attachment"
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


async def _read_upload(upload: UploadFile) -> bytes:
    from app.core.config import get_settings

    data = await upload.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    check_size(data)
    return data


# ------------------------------------------------------------------ kinds & templates
@router.get("/document-kinds")
async def list_kinds(
    user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    out = []
    for module_id, kind in get_registry().document_kinds():
        if not user.has(kind.view_permission):
            continue
        active = await session.scalar(
            select(TemplateVersion).where(
                TemplateVersion.kind == kind.key, TemplateVersion.is_active.is_(True)
            )
        )
        out.append(
            {
                "key": kind.key,
                "module_id": module_id,
                "title": kind.title,
                "prefix": kind.prefix,
                "placeholders": [p.__dict__ for p in kind.placeholders],
                "active_version": active.version if active else None,
                "can_create": user.has(kind.create_permission),
                "can_manage": user.has(kind.manage_permission),
            }
        )
    return out


def _version_out(v: TemplateVersion, f: StoredFile | None) -> dict[str, Any]:
    return {
        "version": v.version,
        "is_active": v.is_active,
        "placeholders": v.placeholders,
        "unknown_placeholders": v.unknown_placeholders,
        "note": v.note,
        "uploaded_by": v.uploaded_by,
        "created_at": v.created_at,
        "filename": f.filename if f else None,
        "size": f.size if f else None,
    }


@router.get("/document-kinds/{kind_key}/templates")
async def list_templates(
    kind_key: str, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    _, kind = service.kind_or_404(kind_key)
    _need(user, kind, "view")
    rows = (
        await session.execute(
            select(TemplateVersion, StoredFile)
            .join(StoredFile, StoredFile.id == TemplateVersion.file_id)
            .where(TemplateVersion.kind == kind.key)
            .order_by(TemplateVersion.version.desc())
        )
    ).all()
    return [_version_out(v, f) for v, f in rows]


@router.post("/document-kinds/{kind_key}/templates", status_code=201)
async def upload_template(
    kind_key: str,
    file: UploadFile = File(...),
    note: str = Form("", max_length=500),
    activate: bool = Form(False),
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    module_id, kind = service.kind_or_404(kind_key)
    _need(user, kind, "manage")
    data = await _read_upload(file)
    sniff(data, {DOCX})
    row = await service.upload_template(
        session, user.actor, kind, module_id, data, file.filename or "template.docx", note
    )
    if activate:
        row = await service.activate_template(session, user.actor, kind, module_id, row.version)
    await session.commit()
    f = await session.get(StoredFile, row.file_id)
    return _version_out(row, f)


@router.post("/document-kinds/{kind_key}/templates/{version}/activate")
async def activate_template(
    kind_key: str,
    version: int,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    module_id, kind = service.kind_or_404(kind_key)
    _need(user, kind, "manage")
    row = await service.activate_template(session, user.actor, kind, module_id, version)
    await session.commit()
    return _version_out(row, await session.get(StoredFile, row.file_id))


@router.get("/document-kinds/{kind_key}/templates/{version}/file")
async def download_template(
    kind_key: str,
    version: int,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    _, kind = service.kind_or_404(kind_key)
    _need(user, kind, "manage")
    row = await session.scalar(
        select(TemplateVersion).where(TemplateVersion.kind == kind.key, TemplateVersion.version == version)
    )
    if row is None:
        raise NotFound("Không tìm thấy phiên bản mẫu.")
    _, data = await read_file(session, row.file_id)
    return _download(data, f"{kind.prefix}-mau-v{version}.docx", DOCX)


@router.get("/document-kinds/{kind_key}/templates/{version}/sample.pdf")
async def sample_template(
    kind_key: str,
    version: int,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    _, kind = service.kind_or_404(kind_key)
    _need(user, kind, "view")
    pdf = await service.sample_pdf(session, kind, version)
    return _download(pdf, f"{kind.prefix}-mau-v{version}-du-lieu-mau.pdf", "application/pdf", inline=True)


# ------------------------------------------------------------------ documents
def document_out(
    doc: Document, kind: DocumentKind | None = None, user: CurrentUser | None = None
) -> dict[str, Any]:
    """Callers pass only records already in `user`'s view scope, so the flags need no extra query."""
    can_edit = can_upload_signed = False
    if user is not None and kind is not None:
        can_edit = (
            kind.view_all_permission is None
            or doc.created_by == user.upn
            or user.has(kind.manage_permission)
            or (doc.job_id is not None and user.has(kind.create_permission))
        )
        can_upload_signed = doc.status in ("finalized", "signed") and user.has(kind.create_permission)
    return {
        "id": str(doc.id),
        "kind": doc.kind,
        "kind_title": kind.title if kind else doc.kind,
        "module_id": doc.module_id,
        "number": doc.number,
        "status": doc.status,
        "subject_key": doc.subject_key,
        "subject_label": doc.subject_label,
        "external_ref": doc.external_ref,
        "data": doc.data,
        "created_by": doc.created_by,
        "job_id": str(doc.job_id) if doc.job_id else None,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "finalized_at": doc.finalized_at,
        "finalized_by": doc.finalized_by,
        "signed_at": doc.signed_at,
        "signed_by": doc.signed_by,
        "voided_at": doc.voided_at,
        "void_reason": doc.void_reason,
        "has_pdf": doc.pdf_file_id is not None,
        "has_signed": doc.signed_file_id is not None,
        "can_edit": can_edit,
        "can_upload_signed": can_upload_signed,
        "events": doc.events,
        "version": doc.version,
    }


async def _doc(
    session: AsyncSession,
    user: CurrentUser,
    doc_id: uuid.UUID,
    which: Literal["view", "create", "manage"],
    for_update: bool = False,
    *,
    draft_edit: bool = False,
) -> tuple[Document, DocumentKind]:
    doc = await service.get_document(session, doc_id, for_update=for_update)
    _, kind = service.kind_or_404(doc.kind)
    _need(user, kind, which)
    await service.check_doc_scope(session, user, kind, doc)
    if draft_edit and not await service.can_edit_draft(session, user, kind, doc):
        raise PermissionDenied("Chỉ người lập hoặc người phụ trách Job mới sửa được biên bản này.")
    return doc, kind


@router.get("/documents")
async def list_documents(
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    kind: str = "",
    status: Literal["", "draft", "finalized", "signed", "void"] = "",
    subject_key: str = "",
    q: str = Query("", max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    kinds = {k.key: k for _, k in get_registry().document_kinds() if user.has(k.view_permission)}
    if kind and kind not in kinds:
        raise PermissionDenied("Bạn không có quyền xem loại biên bản này.")
    keys = [kind] if kind else list(kinds)
    scoped = [
        or_(Document.kind != k, c) for k in keys if (c := service.scope_clause(user, kinds[k])) is not None
    ]
    stmt = select(Document).where(Document.kind.in_(keys), *scoped)
    if status:
        stmt = stmt.where(Document.status == status)
    if subject_key:
        stmt = stmt.where(Document.subject_key == subject_key)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Document.number).like(like),
                func.lower(Document.subject_label).like(like),
                Document.subject_key.like(like),
            )
        )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await session.scalars(stmt.order_by(Document.created_at.desc()).offset(offset).limit(limit))
    ).all()
    return {"total": total, "items": [document_out(d, kinds.get(d.kind), user) for d in rows]}


class CreateIn(BaseModel):
    kind: str
    data: dict[str, Any]


@router.post("/documents", status_code=201)
async def create_document(
    body: CreateIn, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    module_id, kind = service.kind_or_404(body.kind)
    _need(user, kind, "create")
    doc = await service.create_draft(session, user.actor, kind, module_id, body.data)
    await session.commit()
    return document_out(doc, kind, user)


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: uuid.UUID, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    doc, kind = await _doc(session, user, doc_id, "view")
    return document_out(doc, kind, user)


class UpdateIn(BaseModel):
    data: dict[str, Any]
    version: int


@router.put("/documents/{doc_id}")
async def update_document(
    doc_id: uuid.UUID,
    body: UpdateIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc, kind = await _doc(session, user, doc_id, "create", for_update=True, draft_edit=True)
    await service.update_draft(session, user.actor, doc, kind, body.data, body.version)
    await session.commit()
    return document_out(doc, kind, user)


@router.get("/documents/{doc_id}/preview.pdf")
async def preview(
    doc_id: uuid.UUID, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> Response:
    doc, kind = await _doc(session, user, doc_id, "view")
    pdf = await service.preview_pdf(session, doc, kind)
    return _download(pdf, f"{doc.number or 'ban-nhap'}.pdf", "application/pdf", inline=True)


@router.post("/documents/{doc_id}/finalize")
async def finalize(
    doc_id: uuid.UUID, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    doc, kind = await _doc(session, user, doc_id, "create", for_update=True, draft_edit=True)
    await service.finalize(session, user.actor, doc, kind)
    await session.commit()
    return document_out(doc, kind, user)


@router.get("/documents/{doc_id}/file/{which}")
async def download(
    doc_id: uuid.UUID,
    which: Literal["pdf", "docx", "signed"],
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    doc, _ = await _doc(session, user, doc_id, "view")
    file_id = {"pdf": doc.pdf_file_id, "docx": doc.docx_file_id, "signed": doc.signed_file_id}[which]
    if file_id is None:
        raise NotFound("Biên bản chưa có file này.")
    f, data = await read_file(session, file_id)
    return _download(data, f.filename, f.mime, inline=which != "docx")


@router.post("/documents/{doc_id}/signed")
async def upload_signed(
    doc_id: uuid.UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc, kind = await _doc(session, user, doc_id, "create", for_update=True)
    data = await _read_upload(file)
    mime = sniff(data, {"application/pdf", "image/png", "image/jpeg"})
    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[mime]
    await service.attach_signed(session, user.actor, doc, data, f"{doc.number}-da-ky.{ext}", mime)
    await session.commit()
    return document_out(doc, kind, user)


class VoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/documents/{doc_id}/void")
async def void_document(
    doc_id: uuid.UUID,
    body: VoidIn,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc, kind = await _doc(session, user, doc_id, "create", for_update=True, draft_edit=True)
    # Drafts can be discarded by their creators; numbered records need the manage permission.
    if doc.status != "draft":
        _need(user, kind, "manage")
    await service.void(session, user.actor, doc, body.reason)
    await session.commit()
    return document_out(doc, kind, user)
