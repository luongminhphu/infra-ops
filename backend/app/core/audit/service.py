"""Write and verify the shared, append-only audit log.

Every module records security-relevant changes through :func:`record`. Values are
redacted before storage so secrets never reach the audit table.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.models import AuditLog
from app.core.logging import correlation_id_var, redact

GENESIS_HASH = "0" * 64
# Serialises writers so the hash chain stays linear. Audit volume is low (human actions).
_AUDIT_LOCK_KEY = 0x1A0D17

Result = Literal["success", "failure", "denied"]


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID | None
    upn: str | None
    ip: str | None = None

    @classmethod
    def system(cls) -> Actor:
        return cls(user_id=None, upn="system", ip=None)


def _canonical(entry: dict[str, Any]) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False).encode()


def compute_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    return hashlib.sha256(prev_hash.encode() + _canonical(fields)).hexdigest()


def _hash_fields(row: AuditLog) -> dict[str, Any]:
    return {
        "at": row.at.astimezone(UTC).isoformat(),
        "actor_id": str(row.actor_id) if row.actor_id else None,
        "actor_upn": row.actor_upn,
        "actor_ip": row.actor_ip,
        "module_id": row.module_id,
        "system_type": row.system_type,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "target_label": row.target_label,
        "before": row.before,
        "after": row.after,
        "result": row.result,
        "message": row.message,
        "correlation_id": row.correlation_id,
    }


async def record(
    session: AsyncSession,
    *,
    actor: Actor,
    module_id: str,
    action: str,
    result: Result = "success",
    system_type: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    message: str | None = None,
) -> AuditLog:
    """Append an audit entry inside the caller's transaction (commit is the caller's job)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})
    prev = await session.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))
    row = AuditLog(
        at=datetime.now(UTC),
        actor_id=actor.user_id,
        actor_upn=actor.upn,
        actor_ip=actor.ip,
        module_id=module_id,
        system_type=system_type,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        before=redact(before) if before is not None else None,
        after=redact(after) if after is not None else None,
        result=result,
        message=message,
        correlation_id=correlation_id_var.get(),
        prev_hash=prev or GENESIS_HASH,
    )
    # Round-trip JSON so the hashed form equals what JSONB returns later.
    row.before = json.loads(_canonical(row.before)) if row.before is not None else None
    row.after = json.loads(_canonical(row.after)) if row.after is not None else None
    row.hash = compute_hash(row.prev_hash, _hash_fields(row))
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    checked: int
    broken_at_id: int | None = None
    reason: str | None = None


async def verify_chain(session: AsyncSession, batch: int = 2000) -> ChainReport:
    prev = GENESIS_HASH
    last_id = 0
    checked = 0
    while True:
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.id > last_id).order_by(AuditLog.id).limit(batch)
            )
        ).all()
        if not rows:
            return ChainReport(ok=True, checked=checked)
        for row in rows:
            if row.prev_hash != prev:
                return ChainReport(False, checked, row.id, "Liên kết với bản ghi trước bị đứt")
            if compute_hash(row.prev_hash, _hash_fields(row)) != row.hash:
                return ChainReport(False, checked, row.id, "Nội dung bản ghi đã bị thay đổi")
            prev = row.hash
            last_id = row.id
            checked += 1


def diff(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reduce two snapshots to the keys that changed (keeps audit entries readable)."""
    before = before or {}
    after = after or {}
    keys = sorted(set(before) | set(after))
    changed = [k for k in keys if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in changed}, {k: after.get(k) for k in changed}
