"""Give users their own SDP API key (Jobs, receiving and reassigning run with it)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.auth.models import User
from app.core.connections.models import ConnectionProfile, UserCredential
from app.core.connections.service import set_personal_credential
from app.core.db import get_sessionmaker


async def give_personal_keys(profile_id: str | uuid.UUID, *upns: str) -> None:
    """Every listed user (all users when none listed) gets a personal key ``key-<upn>`` for the profile."""
    async with get_sessionmaker()() as s:
        profile = await s.get(ConnectionProfile, uuid.UUID(str(profile_id)))
        assert profile is not None
        stmt = select(User) if not upns else select(User).where(User.upn.in_([u.lower() for u in upns]))
        have = set(
            (
                await s.scalars(select(UserCredential.user_id).where(UserCredential.profile_id == profile.id))
            ).all()
        )
        for user in (await s.scalars(stmt)).all():
            if user.id not in have:
                cred, _ = set_personal_credential(
                    profile, user.id, None, "api_key", {}, {"api_key": f"key-{user.upn}"}
                )
                s.add(cred)
        await s.commit()
