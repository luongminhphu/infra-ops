"""Typed, audited per-module settings.

A module defines a pydantic model with defaults; unsaved settings fall back to those defaults,
and stored values are re-validated on read so a schema change never crashes the module.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor, diff, record
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.settings.models import ModuleSetting

log = get_logger(__name__)


async def load_settings[M: BaseModel](session: AsyncSession, module_id: str, key: str, model: type[M]) -> M:
    row = await session.get(ModuleSetting, (module_id, key))
    if row is None:
        return model()
    try:
        return model.model_validate(row.value)
    except ValidationError:
        # Keep the module running with defaults and make the problem visible in logs.
        log.warning("module_settings_invalid", module_id=module_id, key=key)
        return model()


async def save_settings[M: BaseModel](
    session: AsyncSession, actor: Actor, module_id: str, key: str, model: type[M], data: dict[str, Any]
) -> M:
    try:
        value = model.model_validate(data)
    except ValidationError as exc:
        details = [{"loc": list(map(str, e["loc"])), "msg": e["msg"]} for e in exc.errors()]
        raise AppError("Cấu hình không hợp lệ.", code="invalid_settings", details=details) from exc
    row = await session.get(ModuleSetting, (module_id, key))
    before = row.value if row else model().model_dump(mode="json")
    after = value.model_dump(mode="json")
    if row is None:
        session.add(ModuleSetting(module_id=module_id, key=key, value=after, updated_by=actor.user_id))
    else:
        row.value = after
        row.updated_by = actor.user_id
    b, a = diff(before, after)
    if a:
        await record(
            session,
            actor=actor,
            module_id=module_id,
            action="settings.updated",
            target_type="settings",
            target_id=f"{module_id}.{key}",
            target_label=key,
            before=b,
            after=a,
        )
    await session.flush()
    return value
