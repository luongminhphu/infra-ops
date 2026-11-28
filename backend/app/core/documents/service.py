"""Document lifecycle: draft → finalized (numbered, rendered) → signed (scan uploaded) | void."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, exists, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor, diff, record
from app.core.auth.deps import CurrentUser
from app.core.branding.service import logo_bytes
from app.core.documents.engine import docx_to_pdf, render_docx, template_variables
from app.core.documents.models import Document, TemplateVersion
from app.core.errors import AppError, Conflict, NotFound
from app.core.modules.manifest import DocumentKind
from app.core.modules.registry import ModuleRegistry, get_registry
from app.core.storage.service import read_file, save_file
from app.core.timeutil import TZ
from app.core.workflow.models import Job

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DRAFT_NUMBER = "(bản nháp – chưa có số)"


def kind_or_404(key: str) -> tuple[str, DocumentKind]:
    found = get_registry().document_kind(key)
    if found is None:
        raise NotFound("Loại biên bản không tồn tại.")
    return found


def validate_data(kind: DocumentKind, data: dict[str, Any]) -> BaseModel:
    try:
        return kind.data_model.model_validate(data)
    except ValidationError as exc:
        details = [{"loc": list(map(str, e["loc"])), "msg": e["msg"]} for e in exc.errors()]
        raise AppError("Thông tin biên bản chưa hợp lệ.", code="invalid_document", details=details) from exc


# ------------------------------------------------------------------ templates
async def ensure_default_templates(session: AsyncSession, registry: ModuleRegistry) -> None:
    """Seed version 1 from the module's bundled template the first time a kind is seen."""
    for _, kind in registry.document_kinds():
        exists = await session.scalar(
            select(TemplateVersion.id).where(TemplateVersion.kind == kind.key).limit(1)
        )
        if exists or not kind.default_template.exists():
            continue
        data = kind.default_template.read_bytes()
        file = await save_file(session, data, kind.default_template.name, DOCX_MIME, "system")
        found = template_variables(data)
        session.add(
            TemplateVersion(
                kind=kind.key,
                version=1,
                file_id=file.id,
                is_active=True,
                placeholders=sorted(found),
                unknown_placeholders=_unknown(kind, found),
                note="Mẫu mặc định đi kèm ứng dụng",
                uploaded_by="system",
            )
        )
    await session.flush()


def _unknown(kind: DocumentKind, found: set[str]) -> list[str]:
    known = {p.name.split(".")[0].split("[")[0] for p in kind.placeholders} | {"logo"}  # logo: added by core
    return sorted(found - known)


async def active_template(session: AsyncSession, kind_key: str) -> TemplateVersion:
    row = await session.scalar(
        select(TemplateVersion).where(TemplateVersion.kind == kind_key, TemplateVersion.is_active.is_(True))
    )
    if row is None:
        raise AppError("Chưa có mẫu biên bản đang dùng cho loại này.", code="no_template")
    return row


async def upload_template(
    session: AsyncSession,
    actor: Actor,
    kind: DocumentKind,
    module_id: str,
    data: bytes,
    filename: str,
    note: str,
) -> TemplateVersion:
    found = template_variables(data)
    last = await session.scalar(
        select(TemplateVersion.version)
        .where(TemplateVersion.kind == kind.key)
        .order_by(TemplateVersion.version.desc())
        .limit(1)
    )
    file = await save_file(session, data, filename, DOCX_MIME, actor.upn)
    row = TemplateVersion(
        kind=kind.key,
        version=(last or 0) + 1,
        file_id=file.id,
        is_active=False,
        placeholders=sorted(found),
        unknown_placeholders=_unknown(kind, found),
        note=note[:500],
        uploaded_by=actor.upn,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        actor=actor,
        module_id=module_id,
        action="documents.template_uploaded",
        target_type="template",
        target_id=f"{kind.key}:v{row.version}",
        target_label=f"{kind.title} v{row.version}",
        after={
            "filename": file.filename,
            "placeholders": row.placeholders,
            "unknown": row.unknown_placeholders,
        },
    )
    return row


async def activate_template(
    session: AsyncSession, actor: Actor, kind: DocumentKind, module_id: str, version: int
) -> TemplateVersion:
    row = await session.scalar(
        select(TemplateVersion).where(TemplateVersion.kind == kind.key, TemplateVersion.version == version)
    )
    if row is None:
        raise NotFound("Không tìm thấy phiên bản mẫu.")
    await session.execute(
        update(TemplateVersion).where(TemplateVersion.kind == kind.key).values(is_active=False)
    )
    row.is_active = True
    await record(
        session,
        actor=actor,
        module_id=module_id,
        action="documents.template_activated",
        target_type="template",
        target_id=f"{kind.key}:v{version}",
        target_label=f"{kind.title} v{version}",
    )
    await session.flush()
    return row


async def render(
    session: AsyncSession, kind: DocumentKind, template: TemplateVersion, data: BaseModel, number: str
) -> tuple[bytes, bytes]:
    _, tpl = await read_file(session, template.file_id)
    docx = render_docx(tpl, kind.build_context(data, number), await logo_bytes(session))
    return docx, await docx_to_pdf(docx)


async def sample_pdf(session: AsyncSession, kind: DocumentKind, version: int) -> bytes:
    row = await session.scalar(
        select(TemplateVersion).where(TemplateVersion.kind == kind.key, TemplateVersion.version == version)
    )
    if row is None:
        raise NotFound("Không tìm thấy phiên bản mẫu.")
    _, pdf = await render(
        session, kind, row, validate_data(kind, kind.sample_data), f"{kind.prefix}-MẪU-0000"
    )
    return pdf


# ------------------------------------------------------------------ numbering
async def next_number(session: AsyncSession, prefix: str, at: datetime | None = None) -> str:
    period = (at or datetime.now(TZ)).astimezone(TZ).strftime("%Y%m")
    value = await session.scalar(
        text(
            "INSERT INTO core_document_sequence (prefix, period, last_value) VALUES (:p, :m, 1) "
            "ON CONFLICT (prefix, period) DO UPDATE SET last_value = core_document_sequence.last_value + 1 "
            "RETURNING last_value"
        ),
        {"p": prefix, "m": period},
    )
    return f"{prefix}-{period}-{int(value):04d}"


# ------------------------------------------------------------------ documents
async def get_document(session: AsyncSession, doc_id: uuid.UUID, *, for_update: bool = False) -> Document:
    doc = await session.get(Document, doc_id, with_for_update=for_update)
    if doc is None:
        raise NotFound("Không tìm thấy biên bản.")
    return doc


def scope_clause(user: CurrentUser, kind: DocumentKind) -> Any | None:
    """SQL filter for the records `user` may see; None = all. Same Job rule as `check_job_scope`."""
    if (
        kind.view_all_permission is None
        or user.has(kind.view_all_permission)
        or user.has(kind.manage_permission)
    ):
        return None
    mine = exists().where(
        Job.id == Document.job_id, or_(Job.assignee_user_id.is_(None), Job.assignee_user_id == user.id)
    )
    return or_(Document.created_by == user.upn, and_(Document.job_id.is_not(None), mine))


async def check_doc_scope(
    session: AsyncSession, user: CurrentUser, kind: DocumentKind, doc: Document
) -> None:
    """Out-of-scope records are reported as missing (404), not forbidden."""
    clause = scope_clause(user, kind)
    if (
        clause is not None
        and await session.scalar(select(Document.id).where(Document.id == doc.id, clause)) is None
    ):
        raise NotFound("Không tìm thấy biên bản.")


async def can_edit_draft(session: AsyncSession, user: CurrentUser, kind: DocumentKind, doc: Document) -> bool:
    """Creator, manager, or the person running the Job the record belongs to (the worker creates the draft)."""
    if kind.view_all_permission is None or doc.created_by == user.upn or user.has(kind.manage_permission):
        return True
    if doc.job_id is None or not user.has(kind.create_permission):
        return False
    clause = scope_clause(user, kind)
    return (
        clause is None
        or await session.scalar(select(Document.id).where(Document.id == doc.id, clause)) is not None
    )


async def create_draft(
    session: AsyncSession,
    actor: Actor,
    kind: DocumentKind,
    module_id: str,
    data: dict[str, Any],
    job_id: uuid.UUID | None = None,
) -> Document:
    model = validate_data(kind, data)
    key, label, ref = kind.subject(model)
    doc = Document(
        kind=kind.key,
        module_id=module_id,
        status="draft",
        subject_key=key,
        subject_label=label[:256],
        external_ref=ref,
        data=model.model_dump(mode="json"),
        created_by=actor.upn or "system",
        job_id=job_id,
        events=[],
    )
    session.add(doc)
    await session.flush()
    await record(
        session,
        actor=actor,
        module_id=module_id,
        action="documents.created",
        target_type="document",
        target_id=str(doc.id),
        target_label=f"{kind.title} – {label}",
    )
    return doc


async def update_draft(
    session: AsyncSession, actor: Actor, doc: Document, kind: DocumentKind, data: dict[str, Any], version: int
) -> Document:
    if doc.status != "draft":
        raise Conflict(
            "Biên bản đã chốt, không sửa được. Huỷ và lập biên bản mới nếu cần thay đổi.",
            code="document_locked",
        )
    if version != doc.version:
        raise Conflict("Biên bản vừa được người khác cập nhật. Tải lại trang.", code="stale_version")
    model = validate_data(kind, data)
    before = {"subject_key": doc.subject_key, "external_ref": doc.external_ref, **doc.data}
    doc.data = model.model_dump(mode="json")
    doc.subject_key, label, doc.external_ref = kind.subject(model)
    doc.subject_label = label[:256]
    doc.version += 1
    await session.flush()
    b, a = diff(before, {"subject_key": doc.subject_key, "external_ref": doc.external_ref, **doc.data})
    await record(
        session,
        actor=actor,
        module_id=doc.module_id,
        action="documents.updated",
        target_type="document",
        target_id=str(doc.id),
        target_label=f"{kind.title} – {doc.subject_label}",
        before=b,
        after=a,
    )
    return doc


async def preview_pdf(session: AsyncSession, doc: Document, kind: DocumentKind) -> bytes:
    if doc.status != "draft" and doc.pdf_file_id:
        _, pdf = await read_file(session, doc.pdf_file_id)
        return pdf
    template = await active_template(session, kind.key)
    _, pdf = await render(session, kind, template, validate_data(kind, doc.data), DRAFT_NUMBER)
    return pdf


async def finalize(session: AsyncSession, actor: Actor, doc: Document, kind: DocumentKind) -> Document:
    if doc.status != "draft":
        raise Conflict("Biên bản đã được chốt trước đó.", code="already_finalized")
    template = await active_template(session, kind.key)
    number = await next_number(session, kind.prefix)
    docx, pdf = await render(session, kind, template, validate_data(kind, doc.data), number)
    base = f"{number}"
    docx_file = await save_file(session, docx, f"{base}.docx", DOCX_MIME, actor.upn)
    pdf_file = await save_file(session, pdf, f"{base}.pdf", "application/pdf", actor.upn)
    doc.number = number
    doc.status = "finalized"
    doc.template_version_id = template.id
    doc.docx_file_id = docx_file.id
    doc.pdf_file_id = pdf_file.id
    doc.finalized_at = datetime.now(UTC)
    doc.finalized_by = actor.upn
    doc.version += 1
    await record(
        session,
        actor=actor,
        module_id=doc.module_id,
        action="documents.finalized",
        target_type="document",
        target_id=str(doc.id),
        target_label=f"{number} – {doc.subject_label}",
        after={"number": number, "template_version": template.version},
    )
    return doc


async def attach_signed(
    session: AsyncSession, actor: Actor, doc: Document, data: bytes, filename: str, mime: str
) -> Document:
    if doc.status not in ("finalized", "signed"):
        raise Conflict("Chỉ tải bản đã ký cho biên bản đã chốt.", code="not_finalized")
    file = await save_file(session, data, filename, mime, actor.upn)
    replaced = doc.signed_file_id is not None
    doc.signed_file_id = file.id
    doc.status = "signed"
    doc.signed_at = datetime.now(UTC)
    doc.signed_by = actor.upn
    doc.version += 1
    await record(
        session,
        actor=actor,
        module_id=doc.module_id,
        action="documents.signed_uploaded",
        target_type="document",
        target_id=str(doc.id),
        target_label=f"{doc.number} – {doc.subject_label}",
        after={"filename": file.filename, "size": file.size, "sha256": file.sha256, "replaced": replaced},
    )
    return doc


async def void(session: AsyncSession, actor: Actor, doc: Document, reason: str) -> Document:
    if doc.status == "void":
        raise Conflict("Biên bản đã bị huỷ trước đó.", code="already_void")
    before = doc.status
    doc.status = "void"
    doc.voided_at = datetime.now(UTC)
    doc.void_reason = reason[:500]
    doc.version += 1
    await record(
        session,
        actor=actor,
        module_id=doc.module_id,
        action="documents.voided",
        target_type="document",
        target_id=str(doc.id),
        target_label=f"{doc.number or 'Nháp'} – {doc.subject_label}",
        before={"status": before},
        after={"status": "void", "reason": reason},
    )
    return doc


def add_event(doc: Document, event: dict[str, Any]) -> None:
    doc.events = [*(doc.events or []), {**event, "at": datetime.now(UTC).isoformat()}]
