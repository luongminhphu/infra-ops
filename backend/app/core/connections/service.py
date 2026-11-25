"""Connection profiles and credentials – the public core API modules use to reach external systems."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

from cryptography import x509
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.connections.models import ConnectionProfile, Credential, UserCredential
from app.core.connectors.base import AUTH_TYPES, AuthTypeSpec, ConnectionContext, ConnectorType, Purpose
from app.core.connectors.registry import get_connector
from app.core.errors import AppError, CapabilityUnavailable, NotFound, PersonalCredentialMissing
from app.core.secrets.crypto import Encrypted, get_keyring, secret_hint

PURPOSE_LABEL = {"read": "Read", "write": "Write"}


def credential_aad(profile_id: uuid.UUID, purpose: str) -> str:
    return f"credential:{profile_id}:{purpose}"


def connector_or_404(system_type: str) -> ConnectorType:
    connector = get_connector(system_type)
    if connector is None:
        raise NotFound(f"Loại hệ thống “{system_type}” chưa được hỗ trợ.")
    return connector


def validate_config(connector: ConnectorType, config: dict[str, Any]) -> BaseModel:
    try:
        return connector.config_model.model_validate(config)
    except ValidationError as exc:
        details = [{"loc": ["config", *map(str, e["loc"])], "msg": e["msg"]} for e in exc.errors()]
        raise AppError("Cấu hình kết nối không hợp lệ.", code="invalid_config", details=details) from exc


def certificate_expiry(pem: str) -> datetime | None:
    try:
        cert = x509.load_pem_x509_certificate(pem.encode())
    except ValueError:
        return None
    return cert.not_valid_after_utc


def clean_credential(
    profile: ConnectionProfile,
    auth_type: str,
    public: dict[str, Any],
    secrets: dict[str, Any],
    previous: dict[str, Any],
) -> tuple[AuthTypeSpec, dict[str, str], dict[str, str], bool]:
    """Check the fields of a credential for the profile's connector. Empty secret fields keep ``previous``.

    Returns (spec, public, secrets, secrets_changed).
    """
    connector = connector_or_404(profile.system_type)
    if auth_type not in connector.auth_types:
        raise AppError("Phương thức xác thực không được hỗ trợ cho hệ thống này.", code="invalid_auth_type")
    spec = AUTH_TYPES[auth_type]
    clean_public = {f.name: str(public.get(f.name, "")).strip() for f in spec.public_fields}
    clean_secrets: dict[str, str] = {}
    missing: list[str] = []
    changed = False
    for f in spec.secret_fields:
        new_value = str(secrets.get(f.name) or "")
        if new_value:
            clean_secrets[f.name] = new_value
            changed = changed or previous.get(f.name) != new_value
        elif f.name in previous:
            clean_secrets[f.name] = previous[f.name]
        elif f.required:
            missing.append(f.label)
    for f in spec.public_fields:
        if f.required and not clean_public[f.name]:
            missing.append(f.label)
    if missing:
        raise AppError(f"Thiếu thông tin: {', '.join(missing)}.", code="credential_incomplete")
    return spec, clean_public, clean_secrets, changed


def apply_credential(
    profile: ConnectionProfile,
    existing: Credential | None,
    purpose: str,
    auth_type: str,
    public: dict[str, Any],
    secrets: dict[str, Any],
    expires_at: datetime | None,
    actor_id: uuid.UUID | None,
) -> tuple[Credential, bool]:
    """Validate and encrypt a credential. Empty secret fields keep their stored value (same auth type).

    Returns (credential, secrets_changed).
    """
    keyring = get_keyring()
    aad = credential_aad(profile.id, purpose)
    previous: dict[str, Any] = {}
    if existing is not None and existing.auth_type == auth_type:
        previous = keyring.decrypt_json(Encrypted(existing.key_version, existing.ciphertext), aad)
    spec, clean_public, clean_secrets, changed = clean_credential(
        profile, auth_type, public, secrets, previous
    )

    if auth_type == "certificate" and expires_at is None:
        expires_at = certificate_expiry(clean_secrets.get("certificate_pem", ""))

    primary = spec.secret_fields[0].name
    enc = keyring.encrypt_json(clean_secrets, aad)
    now = datetime.now(UTC)
    if existing is None:
        cred = Credential(profile_id=profile.id, purpose=purpose)
        profile.credentials.append(cred)
        changed = True
    else:
        cred = existing
        changed = changed or existing.auth_type != auth_type
    cred.auth_type = auth_type
    cred.public = clean_public
    cred.ciphertext = enc.blob
    cred.key_version = enc.key_version
    cred.hint = secret_hint(clean_secrets.get(primary, "")) if auth_type != "certificate" else ""
    cred.expires_at = expires_at
    cred.updated_by = actor_id
    if changed:
        cred.rotated_at = now
        cred.last_test_at = None
        cred.last_test_ok = None
        cred.last_test_result = None
    return cred, changed


def build_context(profile: ConnectionProfile, purpose: Purpose, timeout: float) -> ConnectionContext:
    connector = connector_or_404(profile.system_type)
    cred = next((c for c in profile.credentials if c.purpose == purpose.value), None)
    if cred is None:
        raise CapabilityUnavailable(
            f"Kết nối “{profile.name}” chưa có credential {PURPOSE_LABEL[purpose.value]}.",
            details={"profile_id": str(profile.id), "purpose": purpose.value},
        )
    secrets = get_keyring().decrypt_json(
        Encrypted(cred.key_version, cred.ciphertext), credential_aad(profile.id, purpose.value)
    )
    return ConnectionContext(
        base_url=profile.base_url,
        config=connector.config_model.model_validate(profile.config),
        purpose=purpose,
        auth_type=cred.auth_type,
        public=dict(cred.public),
        secrets=secrets,
        timeout=timeout,
    )


async def open_connection(
    session: AsyncSession,
    system_type: str,
    purpose: Purpose,
    *,
    profile_id: uuid.UUID | None = None,
    environment: str = "prod",
    call_timeout: float = 20.0,
    exclude: Collection[uuid.UUID] = (),
    as_user: uuid.UUID | str | None = None,
) -> tuple[ConnectionProfile, ConnectionContext]:
    """Resolve the enabled profile for a system and decrypt the credential for ``purpose``.

    Modules call this instead of touching credentials directly. Raises
    :class:`CapabilityUnavailable` with a user-facing reason when the operation must be blocked.

    ``as_user`` (user id or UPN): work that person is responsible for runs with their own credential for the
    profile (connectors with ``personal_credentials``), never the shared one; :class:`PersonalCredentialMissing`
    when they have not entered it. Background reads (sync) pass nothing and use the shared credential.
    """
    stmt = select(ConnectionProfile).where(ConnectionProfile.system_type == system_type)
    if profile_id:
        stmt = stmt.where(ConnectionProfile.id == profile_id)
    else:
        stmt = stmt.where(ConnectionProfile.environment == environment, ConnectionProfile.enabled.is_(True))
        if exclude:  # automatic choice: skip profiles the caller uses for something else
            stmt = stmt.where(ConnectionProfile.id.not_in(list(exclude)))
    profiles = (await session.scalars(stmt.order_by(ConnectionProfile.created_at))).all()
    if not profiles:
        raise CapabilityUnavailable(f"Chưa cấu hình kết nối cho hệ thống “{system_type}”.")
    profile = profiles[0]
    if not profile.enabled:
        raise CapabilityUnavailable(f"Kết nối “{profile.name}” đang tắt.")
    if as_user is not None and connector_or_404(profile.system_type).personal_credentials:
        return profile, await personal_context(session, profile, as_user, purpose, call_timeout)
    return profile, build_context(profile, purpose, call_timeout)


def personal_aad(user_id: uuid.UUID, profile_id: uuid.UUID) -> str:
    return f"user-credential:{user_id}:{profile_id}"


async def personal_context(
    session: AsyncSession,
    profile: ConnectionProfile,
    user: uuid.UUID | str,
    purpose: Purpose,
    call_timeout: float,
) -> ConnectionContext:
    from app.core.auth.models import User  # auth imports connections elsewhere; keep the module graph flat

    who = await (
        session.get(User, user)
        if isinstance(user, uuid.UUID)
        else session.scalar(select(User).where(User.upn == user.lower()))
    )
    if who is None:
        raise PersonalCredentialMissing(
            f"Không xác định được người chịu trách nhiệm để dùng API key cá nhân cho kết nối “{profile.name}”."
        )
    cred = await session.scalar(
        select(UserCredential).where(
            UserCredential.user_id == who.id, UserCredential.profile_id == profile.id
        )
    )
    if cred is None:
        raise PersonalCredentialMissing(
            f"{who.display_name or who.upn} chưa nhập API key cá nhân cho kết nối “{profile.name}”. "
            "Vào menu tài khoản → Khoá API của tôi để nhập; bước sẽ tự chạy lại.",
            details={"profile_id": str(profile.id), "user": who.upn},
        )
    secrets = get_keyring().decrypt_json(
        Encrypted(cred.key_version, cred.ciphertext), personal_aad(who.id, profile.id)
    )
    return ConnectionContext(
        base_url=profile.base_url,
        config=connector_or_404(profile.system_type).config_model.model_validate(profile.config),
        purpose=purpose,
        auth_type=cred.auth_type,
        public=dict(cred.public),
        secrets=secrets,
        timeout=call_timeout,
    )


def set_personal_credential(
    profile: ConnectionProfile,
    user_id: uuid.UUID,
    existing: UserCredential | None,
    auth_type: str,
    public: dict[str, Any],
    secrets: dict[str, Any],
) -> tuple[UserCredential, bool]:
    """Validate and encrypt a person's own credential. Empty secret fields keep the stored value."""
    keyring = get_keyring()
    aad = personal_aad(user_id, profile.id)
    previous: dict[str, Any] = {}
    if existing is not None and existing.auth_type == auth_type:
        previous = keyring.decrypt_json(Encrypted(existing.key_version, existing.ciphertext), aad)
    spec, clean_public, clean_secrets, changed = clean_credential(
        profile, auth_type, public, secrets, previous
    )
    enc = keyring.encrypt_json(clean_secrets, aad)
    cred = existing or UserCredential(user_id=user_id, profile_id=profile.id)
    changed = changed or existing is None or existing.auth_type != auth_type
    cred.auth_type = auth_type
    cred.public = clean_public
    cred.ciphertext = enc.blob
    cred.key_version = enc.key_version
    cred.hint = secret_hint(clean_secrets.get(spec.secret_fields[0].name, ""))
    if changed:
        cred.last_test_at = None
        cred.last_test_ok = None
    return cred, changed


def credential_summary(cred: Credential, warn_days: int) -> dict[str, Any]:
    days_left = None
    if cred.expires_at:
        days_left = (cred.expires_at - datetime.now(UTC)).days
    return {
        "purpose": cred.purpose,
        "auth_type": cred.auth_type,
        "public": cred.public,
        "hint": cred.hint,
        "expires_at": cred.expires_at,
        "days_left": days_left,
        "expiring": days_left is not None and days_left <= warn_days,
        "rotated_at": cred.rotated_at,
        "last_test_at": cred.last_test_at,
        "last_test_ok": cred.last_test_ok,
        "last_test_result": cred.last_test_result,
    }


def profile_snapshot(profile: ConnectionProfile) -> dict[str, Any]:
    """Audit-friendly view of a profile (no secret material)."""
    return {
        "name": profile.name,
        "system_type": profile.system_type,
        "base_url": profile.base_url,
        "environment": profile.environment,
        "enabled": profile.enabled,
        "config": profile.config,
        "notes": profile.notes,
    }


async def personal_key_item(
    session: AsyncSession, user_id: uuid.UUID, system_type: str, profile_id: uuid.UUID | None
) -> dict[str, Any] | None:
    """A "Việc tiếp theo" item asking the person for their own key, or None when they have it (or no profile)."""
    stmt = select(ConnectionProfile).where(ConnectionProfile.system_type == system_type)
    stmt = (
        stmt.where(ConnectionProfile.id == profile_id)
        if profile_id
        else stmt.where(ConnectionProfile.enabled.is_(True))
    )
    profile = (await session.scalars(stmt.order_by(ConnectionProfile.created_at))).first()
    if profile is None or not connector_or_404(system_type).personal_credentials:
        return None
    has = await session.scalar(
        select(UserCredential.id).where(
            UserCredential.user_id == user_id, UserCredential.profile_id == profile.id
        )
    )
    if has:
        return None
    return {
        "key": f"me:key:{profile.id}",
        "kind": "personal_key",
        "priority": 0,
        "title": f"Nhập API key cá nhân cho {profile.name}",
        "detail": "Job, nhận hàng và chuyển việc ghi vào hệ thống bằng key của chính bạn; chưa có key thì các bước đó chờ.",
        "due": None,
        "href": "/me/credentials",
        "action": "Nhập key",
    }
