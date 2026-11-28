"""Infra Playbook API: guides (draft + published versions) and the images pasted into step notes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AppError, Conflict, NotFound
from app.core.modules.registry import get_registry
from app.core.storage.service import check_size, read_file, save_file, sniff
from app.modules.playbook.models import Guide, GuideImage, GuideVersion
from app.modules.playbook.run import run_tree
from app.modules.playbook.templates import TEMPLATES
from app.modules.playbook.tree import Tree, job_refs, starter, unnamed_switch_branches, unreachable

MODULE_ID = "playbook"
VIEW, EDIT, PUBLISH = "playbook.guides.view", "playbook.guides.edit", "playbook.guides.publish"
IMAGE_MIME = {"image/png", "image/jpeg"}

router = APIRouter(tags=["playbook"])
view = require(VIEW)
edit = require(EDIT)
publish_perm = require(PUBLISH)


def _summary(g: Guide, editor: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(g.id),
        "title": g.title,
        "category": g.category,
        "description": g.description,
        "published_version": g.published_version,
    }
    if editor:
        out |= {"draft_updated_at": g.draft_updated_at, "draft_updated_by": g.draft_updated_by}
    return out


async def _guide(session: AsyncSession, guide_id: uuid.UUID, *, lock: bool = False) -> Guide:
    g = await session.get(Guide, guide_id, with_for_update=lock, populate_existing=lock)
    if g is None:
        raise NotFound("Không tìm thấy hướng dẫn.")
    return g


@router.get("/guides")
async def list_guides(
    user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    q: str = Query("", max_length=100),
) -> dict[str, Any]:
    """Readers see published guides; editors also see drafts that were never published."""
    editor = user.has(EDIT)
    stmt = select(Guide).order_by(func.lower(Guide.category), func.lower(Guide.title))
    if not editor:
        stmt = stmt.where(Guide.published_version.is_not(None))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Guide.title).like(like), func.lower(Guide.category).like(like),
                              func.lower(Guide.description).like(like)))  # fmt: skip
    return {"items": [_summary(g, editor) for g in (await session.scalars(stmt)).all()], "can_edit": editor,
            "can_publish": user.has(PUBLISH)}  # fmt: skip


class GuideIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field("", max_length=100)
    description: str = Field("", max_length=1000)


class NewGuideIn(GuideIn):
    template: str | None = Field(None, max_length=40)  # start from a built-in template (TEMPLATES key)


@router.get("/templates")
async def templates(_: CurrentUser = Depends(edit)) -> list[dict[str, Any]]:
    return [{"key": k, "title": t["title"], "category": t["category"], "description": t["description"],
             "steps": len(t["tree"]["nodes"])} for k, t in TEMPLATES.items()]  # fmt: skip


@router.post("/guides", status_code=201)
async def create_guide(
    body: NewGuideIn, user: CurrentUser = Depends(edit), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    tpl = TEMPLATES.get(body.template or "")
    if body.template and tpl is None:
        raise NotFound("Không có mẫu này.")
    g = Guide(
        title=body.title.strip(),
        category=body.category.strip() or (tpl["category"] if tpl else ""),
        description=body.description.strip() or (tpl["description"] if tpl else ""),
        draft=Tree.model_validate(tpl["tree"]).model_dump() if tpl else starter(body.title.strip()),
        created_by=user.upn,
        draft_updated_by=user.upn,
    )
    session.add(g)
    await session.flush()
    await record(session, actor=user.actor, module_id=MODULE_ID, action="playbook.guide_created",
                 target_type="guide", target_id=str(g.id), target_label=g.title)  # fmt: skip
    await session.commit()
    return _summary(g, True)


@router.get("/guides/{guide_id}")
async def get_guide(
    guide_id: uuid.UUID,
    user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
    version: str = Query("published", pattern=r"^(published|draft|\d{1,6})$"),
) -> dict[str, Any]:
    g = await _guide(session, guide_id)
    out = _summary(g, user.has(EDIT))
    if version == "draft":
        if not user.has(EDIT):
            raise NotFound("Không tìm thấy hướng dẫn.")
        return out | {"version": None, "tree": g.draft, "draft_updated_at": g.draft_updated_at}
    number = g.published_version if version == "published" else int(version)
    v = None
    if number is not None:
        v = await session.scalar(
            select(GuideVersion).where(GuideVersion.guide_id == g.id, GuideVersion.version == number)
        )
    if v is None:
        raise NotFound(
            "Hướng dẫn chưa được xuất bản." if version == "published" else "Không có phiên bản này."
        )
    return out | {"version": v.version, "tree": v.tree, "published_at": v.published_at,
                  "published_by": v.published_by, "title": v.title}  # fmt: skip


class DraftIn(GuideIn):
    tree: Tree
    # The draft the editor started from: a newer save by someone else is not overwritten.
    base_updated_at: datetime


@router.put("/guides/{guide_id}/draft")
async def save_draft(
    guide_id: uuid.UUID,
    body: DraftIn,
    user: CurrentUser = Depends(edit),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    g = await _guide(session, guide_id, lock=True)
    if abs((g.draft_updated_at - body.base_updated_at).total_seconds()) > 0.001:
        raise Conflict(
            f"{g.draft_updated_by or 'Người khác'} vừa lưu bản nháp này. Tải lại để xem thay đổi trước khi lưu.",
            code="stale_draft",
        )
    g.title, g.category, g.description = body.title.strip(), body.category.strip(), body.description.strip()
    g.draft = body.tree.model_dump()
    g.draft_updated_at = func.now()
    g.draft_updated_by = user.upn
    await record(session, actor=user.actor, module_id=MODULE_ID, action="playbook.draft_saved",
                 target_type="guide", target_id=str(g.id), target_label=g.title,
                 message=f"{len(body.tree.nodes)} bước")  # fmt: skip
    await session.commit()
    await session.refresh(g)
    return _summary(g, True) | {"tree": g.draft, "draft_updated_at": g.draft_updated_at}


class PublishIn(BaseModel):
    note: str = Field("", max_length=1000)  # what changed, shown in the history


@router.post("/guides/{guide_id}/publish", status_code=201)
async def publish(
    guide_id: uuid.UUID,
    body: PublishIn,
    user: CurrentUser = Depends(publish_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    g = await _guide(session, guide_id, lock=True)
    tree = Tree.model_validate(g.draft)
    lost = unreachable(tree)
    if lost:
        raise AppError(
            f"Có {len(lost)} bước không nối với sơ đồ: {', '.join(lost[:5])}. Nối hoặc xoá trước khi xuất bản.",
            code="unreachable_steps",
        )
    if unnamed := unnamed_switch_branches(tree):
        raise AppError(
            f"Bước rẽ nhánh chưa đặt tên nhánh: {', '.join(unnamed[:5])}. Tên nhánh là giá trị dữ liệu, vd. Quản lý, "
            "hoặc “Khác” cho các giá trị còn lại.",
            code="unnamed_switch_branch",
        )
    if broken := unknown_job_steps(tree):
        raise AppError(
            f"Bước của Job không còn tồn tại: {', '.join(broken[:5])}. Chọn lại bước Job trước khi xuất bản.",
            code="unknown_job_step",
        )
    number = (
        await session.scalar(select(func.max(GuideVersion.version)).where(GuideVersion.guide_id == g.id)) or 0
    ) + 1
    session.add(GuideVersion(guide_id=g.id, version=number, title=g.title, tree=tree.model_dump(),
                             note=body.note.strip(), published_by=user.upn))  # fmt: skip
    before = g.published_version
    g.published_version = number
    await record(session, actor=user.actor, module_id=MODULE_ID, action="playbook.guide_published",
                 target_type="guide", target_id=str(g.id), target_label=g.title,
                 before={"version": before}, after={"version": number}, message=body.note.strip() or None)  # fmt: skip
    await session.commit()
    return _summary(g, True)


def unknown_job_steps(tree: Tree) -> list[str]:
    """Titles of "Bước của Job" steps pointing to a workflow / step the app no longer has (or whose module is off)."""
    steps = {w.key: {s.key for s in w.steps} for _, w in get_registry().workflows()}
    return [
        tree.nodes[k].title for k, ref in job_refs(tree) if ref.step not in steps.get(ref.workflow, set())
    ]


@router.get("/step-guide")
async def step_guide(
    workflow: str = Query(..., max_length=64),
    step: str = Query(..., max_length=64),
    _: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The published Playbook step that explains a Job step (Job page → "Hướng dẫn"): the first guide, by title,
    whose published map has a "Bước của Job" for it."""
    rows = (
        await session.execute(
            select(Guide, GuideVersion)
            .join(
                GuideVersion,
                (GuideVersion.guide_id == Guide.id) & (GuideVersion.version == Guide.published_version),
            )
            .order_by(Guide.title)
        )
    ).all()
    for g, v in rows:
        for node_id, ref in job_refs(Tree.model_validate(v.tree)):
            if (ref.workflow, ref.step) == (workflow, step):
                return {"guide_id": str(g.id), "title": g.title, "node_id": node_id}
    raise NotFound("Chưa có Playbook hướng dẫn bước này.")


@router.get("/facts")
async def facts(_: CurrentUser = Depends(view)) -> list[dict[str, Any]]:
    """Data a check step can test, declared by the enabled modules."""
    return [{"key": f.key, "label": f.label, "kind": f.kind, "param_label": f.param_label, "param_hint": f.param_hint}
            for f in get_registry().facts().values()]  # fmt: skip


class RunIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=20, pattern=r"^[0-9A-Za-z]+$")
    version: str = Field("published", pattern=r"^(published|draft)$")
    choices: dict[str, int] = Field(default_factory=dict, max_length=100)


@router.post("/guides/{guide_id}/run")
async def run_guide(
    guide_id: uuid.UUID,
    body: RunIn,
    user: CurrentUser = Depends(view),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Follow the guide for one employee: checks are decided from the facts (read only, nothing is changed)."""
    g = await _guide(session, guide_id)
    if body.version == "draft":
        if not user.has(EDIT):
            raise NotFound("Không tìm thấy hướng dẫn.")
        tree = Tree.model_validate(g.draft)
    else:
        v = await session.scalar(
            select(GuideVersion).where(
                GuideVersion.guide_id == g.id, GuideVersion.version == g.published_version
            )
        )
        if v is None:
            raise NotFound("Hướng dẫn chưa được xuất bản.")
        tree = Tree.model_validate(v.tree)
    registry = get_registry().facts()
    emp = (
        body.employee_id.zfill(6)
        if body.employee_id.isdigit() and len(body.employee_id) < 6
        else body.employee_id
    )

    async def load(key: str, param: str) -> Any:
        src = registry.get(key)
        return None if src is None else await src.load(session, emp, param)

    result = await run_tree(tree, load, body.choices, {k: f.label for k, f in registry.items()})
    return result | {"employee_id": emp}


@router.get("/guides/{guide_id}/versions")
async def versions(
    guide_id: uuid.UUID, _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    await _guide(session, guide_id)
    rows = (await session.scalars(select(GuideVersion).where(GuideVersion.guide_id == guide_id)
                                  .order_by(GuideVersion.version.desc()))).all()  # fmt: skip
    return [{"version": v.version, "note": v.note, "published_at": v.published_at, "published_by": v.published_by,
             "steps": len(v.tree.get("nodes", {}))} for v in rows]  # fmt: skip


@router.delete("/guides/{guide_id}", status_code=204)
async def delete_guide(
    guide_id: uuid.UUID,
    user: CurrentUser = Depends(publish_perm),
    session: AsyncSession = Depends(get_session),
) -> None:
    g = await _guide(session, guide_id, lock=True)
    await record(session, actor=user.actor, module_id=MODULE_ID, action="playbook.guide_deleted",
                 target_type="guide", target_id=str(g.id), target_label=g.title)  # fmt: skip
    await session.delete(g)
    await session.commit()


@router.post("/guides/{guide_id}/images", status_code=201)
async def upload_image(
    guide_id: uuid.UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(edit),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """A screenshot for a step's note (PNG/JPEG, checked by its bytes). Returns the URL to put in the note."""
    g = await _guide(session, guide_id)
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    check_size(data)
    mime = sniff(data, IMAGE_MIME)
    stored = await save_file(session, data, file.filename or "anh.png", mime, user.upn)
    img = GuideImage(guide_id=g.id, file_id=stored.id, created_by=user.upn)
    session.add(img)
    await session.flush()
    await record(session, actor=user.actor, module_id=MODULE_ID, action="playbook.image_added",
                 target_type="guide", target_id=str(g.id), target_label=g.title, message=stored.filename)  # fmt: skip
    await session.commit()
    return {"id": str(img.id), "url": f"/api/playbook/images/{img.id}"}


@router.get("/images/{image_id}")
async def get_image(
    image_id: uuid.UUID, _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> Response:
    img = await session.get(GuideImage, image_id)
    if img is None:
        raise NotFound("Không tìm thấy ảnh.")
    stored, data = await read_file(session, img.file_id)
    return Response(data, media_type=stored.mime, headers={"Cache-Control": "private, max-age=86400",
                                                            "X-Content-Type-Options": "nosniff"})  # fmt: skip
