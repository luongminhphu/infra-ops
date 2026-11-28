"""Hệ thống › Logo báo cáo: upload / remove the company logo; anyone signed in may fetch it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.deps import CurrentUser, current_user, require
from app.core.branding import service
from app.core.db import get_session
from app.core.errors import NotFound
from app.core.permissions import BRANDING_MANAGE
from app.core.storage.service import read_file

router = APIRouter(tags=["core"])


@router.get("/branding")
async def branding(
    _: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    b = await service.load(session)
    return {
        "has_logo": b.logo_file_id is not None,
        "logo_version": str(b.logo_file_id) if b.logo_file_id else None,
    }


@router.get("/branding/logo")
async def get_logo(
    _: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> Response:
    b = await service.load(session)
    if b.logo_file_id is None:
        raise NotFound("Chưa có logo.")
    f, data = await read_file(session, b.logo_file_id)
    return Response(data, media_type=f.mime, headers={"Cache-Control": "private, max-age=300"})


@router.post("/branding/logo")
async def upload_logo(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require(BRANDING_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    data = await file.read(service.MAX_BYTES + 1)
    b = await service.set_logo(session, user.actor, data)
    await session.commit()
    return {"has_logo": True, "logo_version": str(b.logo_file_id)}


@router.delete("/branding/logo")
async def delete_logo(
    user: CurrentUser = Depends(require(BRANDING_MANAGE)), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await service.clear_logo(session, user.actor)
    await session.commit()
    return {"has_logo": False, "logo_version": None}
