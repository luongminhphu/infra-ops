from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import diff, record
from app.core.auth.deps import CurrentUser, require
from app.core.config import get_settings
from app.core.connections.models import ConnectionProfile
from app.core.connections.service import (
    apply_credential,
    build_context,
    connector_or_404,
    credential_summary,
    profile_snapshot,
    validate_config,
)
from app.core.connectors.base import Purpose, TestReport, TestStep
from app.core.connectors.registry import all_connectors
from app.core.db import get_session
from app.core.errors import AppError, Conflict, NotFound
from app.core.logging import get_logger
from app.core.modules.registry import get_registry
from app.core.permissions import CONNECTIONS_MANAGE, CONNECTIONS_TEST, CONNECTIONS_VIEW
from app.core.secrets.crypto import DecryptionError

router = APIRouter(tags=["connections"])
log = get_logger(__name__)
view = require(CONNECTIONS_VIEW)
manage = require(CONNECTIONS_MANAGE)
test_perm = require(CONNECTIONS_TEST)


@router.get("/connectors")
async def list_connector_types(_: CurrentUser = Depends(view)) -> list[dict[str, Any]]:
    return [c.describe() for c in all_connectors()]


class ProfileOut(BaseModel):
    id: uuid.UUID
    system_type: str
    system_name: str
    name: str
    base_url: str
    environment: str
    enabled: bool
    config: dict[str, Any]
    notes: str
    version: int
    created_at: datetime
    updated_at: datetime
    used_by: list[dict[str, str]]
    credentials: dict[str, dict[str, Any] | None]
    status: Literal["ok", "warning", "error", "untested", "disabled", "incomplete"]
    status_text: str


def _status(profile: ConnectionProfile, creds: dict[str, dict[str, Any] | None]) -> tuple[str, str]:
    if not profile.enabled:
        return "disabled", "Đang tắt"
    present = [c for c in creds.values() if c]
    if not present:
        return "incomplete", "Chưa nhập credential"
    if any(c["last_test_ok"] is False for c in present):
        return "error", "Kiểm tra gần nhất thất bại"
    if any(c["days_left"] is not None and c["days_left"] < 0 for c in present):
        return "error", "Credential đã hết hạn"
    if any(c["expiring"] for c in present):
        return "warning", "Credential sắp hết hạn"
    if any(c["last_test_ok"] is None for c in present):
        return "untested", "Chưa kiểm tra"
    return "ok", "Hoạt động bình thường"


def _out(profile: ConnectionProfile) -> ProfileOut:
    warn = get_settings().secret_expiry_warn_days
    connector = connector_or_404(profile.system_type)
    creds: dict[str, dict[str, Any] | None] = {"read": None, "write": None}
    for c in profile.credentials:
        creds[c.purpose] = credential_summary(c, warn)
    status, text = _status(profile, creds)
    return ProfileOut(
        id=profile.id,
        system_type=profile.system_type,
        system_name=connector.display_name,
        name=profile.name,
        base_url=profile.base_url,
        environment=profile.environment,
        enabled=profile.enabled,
        config=profile.config,
        notes=profile.notes,
        version=profile.version,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        used_by=[{"id": m.id, "name": m.name} for m in get_registry().modules_using(profile.system_type)],
        credentials=creds,
        status=status,
        status_text=text,  # type: ignore[arg-type]
    )


async def _get(session: AsyncSession, profile_id: uuid.UUID) -> ConnectionProfile:
    profile = await session.get(ConnectionProfile, profile_id)
    if profile is None:
        raise NotFound("Không tìm thấy kết nối.")
    return profile


@router.get("/connections", response_model=list[ProfileOut])
async def list_profiles(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> list[ProfileOut]:
    profiles = (await session.scalars(select(ConnectionProfile).order_by(ConnectionProfile.name))).all()
    known = {c.system_type for c in all_connectors()}
    # Profiles of connector types no longer installed are hidden rather than breaking the list.
    return [_out(p) for p in profiles if p.system_type in known]


class ProfileIn(BaseModel):
    system_type: str
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=3, max_length=512, pattern=r"^https?://\S+$")
    environment: Literal["prod", "test"] = "prod"
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=4000)


class ProfilePatch(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=3, max_length=512, pattern=r"^https?://\S+$")
    environment: Literal["prod", "test"] | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    notes: str | None = Field(default=None, max_length=4000)


async def _ensure_unique_name(session: AsyncSession, name: str, exclude: uuid.UUID | None = None) -> None:
    existing = await session.scalar(select(ConnectionProfile).where(ConnectionProfile.name == name))
    if existing is not None and existing.id != exclude:
        raise Conflict("Tên kết nối đã được dùng.")


@router.post("/connections", response_model=ProfileOut, status_code=201)
async def create_profile(
    body: ProfileIn, user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> ProfileOut:
    connector = connector_or_404(body.system_type)
    config = validate_config(connector, body.config)
    await _ensure_unique_name(session, body.name)
    profile = ConnectionProfile(
        system_type=body.system_type,
        name=body.name,
        base_url=body.base_url.rstrip("/"),
        environment=body.environment,
        enabled=body.enabled,
        config=config.model_dump(mode="json"),
        notes=body.notes,
        created_by=user.id,
        updated_by=user.id,
        credentials=[],
    )
    session.add(profile)
    await session.flush()
    await record(
        session,
        actor=user.actor,
        module_id="core",
        system_type=profile.system_type,
        action="connection.created",
        target_type="connection",
        target_id=str(profile.id),
        target_label=profile.name,
        after=profile_snapshot(profile),
    )
    await session.commit()
    await session.refresh(profile)
    return _out(profile)


@router.get("/connections/export")
async def export_profiles(
    user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    profiles = (await session.scalars(select(ConnectionProfile).order_by(ConnectionProfile.name))).all()
    await record(
        session,
        actor=user.actor,
        module_id="core",
        action="connection.exported",
        message=f"{len(profiles)} connections",
    )
    await session.commit()
    return {
        "format": "infra-ops.connections/v1",
        "exported_at": datetime.now(UTC).isoformat(),
        "connections": [
            {
                **profile_snapshot(p),
                # Non-secret identifiers only; secrets must be re-entered after import.
                "credentials": [
                    {"purpose": c.purpose, "auth_type": c.auth_type, "public": c.public}
                    for c in p.credentials
                ],
            }
            for p in profiles
        ],
    }


class ImportBody(BaseModel):
    format: Literal["infra-ops.connections/v1"]
    connections: list[dict[str, Any]]


@router.post("/connections/import")
async def import_profiles(
    body: ImportBody, user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    created, updated, errors = [], [], []
    for item in body.connections:
        name = str(item.get("name", ""))
        try:
            data = ProfileIn.model_validate({k: item.get(k) for k in ProfileIn.model_fields if k in item})
            connector = connector_or_404(data.system_type)
            config = validate_config(connector, data.config).model_dump(mode="json")
        except (AppError, ValueError) as exc:
            errors.append({"name": name, "error": getattr(exc, "message", str(exc))})
            continue
        existing = await session.scalar(select(ConnectionProfile).where(ConnectionProfile.name == data.name))
        if existing is not None:
            if existing.system_type != data.system_type:
                errors.append({"name": name, "error": "Trùng tên với kết nối khác loại hệ thống."})
                continue
            before = profile_snapshot(existing)
            existing.base_url = data.base_url.rstrip("/")
            existing.environment = data.environment
            existing.enabled = data.enabled
            existing.config = config
            existing.notes = data.notes
            existing.updated_by = user.id
            existing.version += 1
            b, a = diff(before, profile_snapshot(existing))
            await record(
                session,
                actor=user.actor,
                module_id="core",
                system_type=existing.system_type,
                action="connection.imported",
                target_type="connection",
                target_id=str(existing.id),
                target_label=existing.name,
                before=b,
                after=a,
            )
            updated.append(name)
        else:
            profile = ConnectionProfile(
                system_type=data.system_type,
                name=data.name,
                base_url=data.base_url.rstrip("/"),
                environment=data.environment,
                enabled=data.enabled,
                config=config,
                notes=data.notes,
                created_by=user.id,
                updated_by=user.id,
                credentials=[],
            )
            session.add(profile)
            await session.flush()
            await record(
                session,
                actor=user.actor,
                module_id="core",
                system_type=profile.system_type,
                action="connection.imported",
                target_type="connection",
                target_id=str(profile.id),
                target_label=profile.name,
                after=profile_snapshot(profile),
            )
            created.append(name)
    await session.commit()
    return {"created": created, "updated": updated, "errors": errors}


@router.get("/connections/{profile_id}", response_model=ProfileOut)
async def get_profile(
    profile_id: uuid.UUID, _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> ProfileOut:
    return _out(await _get(session, profile_id))


@router.patch("/connections/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: uuid.UUID,
    body: ProfilePatch,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    profile = await _get(session, profile_id)
    if body.version != profile.version:
        raise Conflict(
            "Kết nối vừa được người khác cập nhật. Tải lại trang để xem thay đổi mới nhất.",
            code="stale_version",
        )
    before = profile_snapshot(profile)
    if body.name is not None and body.name != profile.name:
        await _ensure_unique_name(session, body.name, exclude=profile.id)
        profile.name = body.name
    if body.base_url is not None:
        profile.base_url = body.base_url.rstrip("/")
    if body.environment is not None:
        profile.environment = body.environment
    if body.enabled is not None:
        profile.enabled = body.enabled
    if body.config is not None:
        profile.config = validate_config(connector_or_404(profile.system_type), body.config).model_dump(
            mode="json"
        )
    if body.notes is not None:
        profile.notes = body.notes
    b, a = diff(before, profile_snapshot(profile))
    if a:
        profile.version += 1
        profile.updated_by = user.id
        await record(
            session,
            actor=user.actor,
            module_id="core",
            system_type=profile.system_type,
            action="connection.updated",
            target_type="connection",
            target_id=str(profile.id),
            target_label=profile.name,
            before=b,
            after=a,
        )
    await session.commit()
    await session.refresh(profile)
    return _out(profile)


@router.delete("/connections/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: uuid.UUID, user: CurrentUser = Depends(manage), session: AsyncSession = Depends(get_session)
) -> None:
    profile = await _get(session, profile_id)
    await record(
        session,
        actor=user.actor,
        module_id="core",
        system_type=profile.system_type,
        action="connection.deleted",
        target_type="connection",
        target_id=str(profile.id),
        target_label=profile.name,
        before=profile_snapshot(profile),
    )
    await session.delete(profile)
    await session.commit()


class CredentialIn(BaseModel):
    auth_type: str
    public: dict[str, Any] = Field(default_factory=dict)
    # Write-only. Empty values keep the stored secret (same auth type).
    secrets: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime | None = None


@router.put("/connections/{profile_id}/credentials/{purpose}", response_model=ProfileOut)
async def put_credential(
    profile_id: uuid.UUID,
    purpose: Purpose,
    body: CredentialIn,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    profile = await _get(session, profile_id)
    existing = next((c for c in profile.credentials if c.purpose == purpose.value), None)
    before = credential_summary(existing, 0) if existing else None
    try:
        cred, changed = apply_credential(
            profile,
            existing,
            purpose.value,
            body.auth_type,
            body.public,
            body.secrets,
            body.expires_at,
            user.id,
        )
    except DecryptionError as exc:
        raise AppError(
            "Không giải mã được secret cũ (master key đã đổi?). Nhập lại toàn bộ secret.",
            code="decrypt_failed",
        ) from exc
    await session.flush()
    after = credential_summary(cred, 0)
    keep = ("auth_type", "public", "hint", "expires_at")
    await record(
        session,
        actor=user.actor,
        module_id="core",
        system_type=profile.system_type,
        action="connection.credential_rotated" if existing and changed else "connection.credential_set",
        target_type="connection",
        target_id=str(profile.id),
        target_label=f"{profile.name} ({purpose.value})",
        before={k: before[k] for k in keep} if before else None,
        after={**{k: after[k] for k in keep}, "secret_changed": changed},
    )
    await session.commit()
    await session.refresh(profile)
    return _out(profile)


@router.delete("/connections/{profile_id}/credentials/{purpose}", response_model=ProfileOut)
async def delete_credential(
    profile_id: uuid.UUID,
    purpose: Purpose,
    user: CurrentUser = Depends(manage),
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    profile = await _get(session, profile_id)
    cred = next((c for c in profile.credentials if c.purpose == purpose.value), None)
    if cred is None:
        raise NotFound("Credential không tồn tại.")
    profile.credentials.remove(cred)
    await record(
        session,
        actor=user.actor,
        module_id="core",
        system_type=profile.system_type,
        action="connection.credential_removed",
        target_type="connection",
        target_id=str(profile.id),
        target_label=f"{profile.name} ({purpose.value})",
    )
    await session.commit()
    await session.refresh(profile)
    return _out(profile)


@router.post("/connections/{profile_id}/test")
async def test_profile(
    profile_id: uuid.UUID,
    purpose: Purpose = Purpose.READ,
    user: CurrentUser = Depends(test_perm),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    profile = await _get(session, profile_id)
    connector = connector_or_404(profile.system_type)
    cred = next((c for c in profile.credentials if c.purpose == purpose.value), None)
    if cred is None:
        report = TestReport()
        report.add(
            TestStep(
                "credential",
                "Credential",
                "fail",
                f"Chưa nhập credential {purpose.value.capitalize()}.",
                "Nhập credential rồi kiểm tra lại.",
            )
        )
        return report.to_dict()
    try:
        ctx = build_context(profile, purpose, get_settings().connector_timeout_seconds)
        report = await connector.test_connection(ctx)
    except DecryptionError:
        report = TestReport()
        report.add(
            TestStep(
                "credential",
                "Giải mã secret",
                "fail",
                "Không giải mã được secret.",
                "Master key có thể đã thay đổi. Nhập lại secret.",
            )
        )
    except Exception as exc:  # A buggy connector must not take down the API.
        log.exception("connection_test_crashed", system_type=profile.system_type)
        report = TestReport()
        report.add(
            TestStep(
                "internal",
                "Kiểm tra",
                "fail",
                f"Lỗi không mong đợi: {type(exc).__name__}",
                "Xem log máy chủ với mã yêu cầu (X-Request-ID).",
            )
        )
    result = report.to_dict()
    result["tested_at"] = datetime.now(UTC).isoformat()
    cred.last_test_at = datetime.now(UTC)
    cred.last_test_ok = report.ok
    cred.last_test_result = result
    await record(
        session,
        actor=user.actor,
        module_id="core",
        system_type=profile.system_type,
        action="connection.tested",
        target_type="connection",
        target_id=str(profile.id),
        target_label=f"{profile.name} ({purpose.value})",
        result="success" if report.ok else "failure",
        after={"steps": [{"key": s.key, "status": s.status} for s in report.steps]},
    )
    await session.commit()
    return result
