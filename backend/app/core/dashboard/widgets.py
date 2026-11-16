"""Core dashboard widgets: connection health and expiring secrets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.connections.models import ConnectionProfile, Credential
from app.core.connectors.registry import get_connector


async def connections_status(session: AsyncSession) -> dict[str, Any]:
    profiles = (await session.scalars(select(ConnectionProfile).order_by(ConnectionProfile.name))).all()
    items = []
    for p in profiles:
        connector = get_connector(p.system_type)
        creds = {c.purpose: c for c in p.credentials}
        tested = [c for c in creds.values() if c.last_test_at]
        if not p.enabled:
            state = "disabled"
        elif not creds:
            state = "incomplete"
        elif any(c.last_test_ok is False for c in creds.values()):
            state = "error"
        elif len(tested) < len(creds):
            state = "untested"
        else:
            state = "ok"
        items.append(
            {
                "id": str(p.id),
                "name": p.name,
                "system_name": connector.display_name if connector else p.system_type,
                "state": state,
                "read": "read" in creds,
                "write": "write" in creds,
                "last_test_at": max((c.last_test_at for c in tested), default=None),
            }
        )
    return {"items": items}


async def expiring_secrets(session: AsyncSession) -> dict[str, Any]:
    warn_days = get_settings().secret_expiry_warn_days
    limit = datetime.now(UTC) + timedelta(days=warn_days)
    rows = (
        await session.execute(
            select(Credential, ConnectionProfile)
            .join(ConnectionProfile, ConnectionProfile.id == Credential.profile_id)
            .where(Credential.expires_at.is_not(None), Credential.expires_at <= limit)
            .order_by(Credential.expires_at)
        )
    ).all()
    now = datetime.now(UTC)
    return {
        "warn_days": warn_days,
        "items": [
            {
                "profile_id": str(p.id),
                "name": p.name,
                "purpose": c.purpose,
                "expires_at": c.expires_at,
                "days_left": (c.expires_at - now).days if c.expires_at else None,
            }
            for c, p in rows
        ],
    }
