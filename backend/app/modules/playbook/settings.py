"""Playbook settings: which published guide a Job workflow shows (Manager's choice, audited)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings.service import load_settings

MODULE_ID = "playbook"
SETTINGS_KEY = "job_guides"


class JobGuides(BaseModel):
    # workflow key -> guide shown on the Job page of that workflow
    workflow_guides: dict[str, uuid.UUID] = Field(default_factory=dict)


async def load(session: AsyncSession) -> JobGuides:
    return await load_settings(session, MODULE_ID, SETTINGS_KEY, JobGuides)
