"""Module settings (stored via core module settings, edited in the UI)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

MODULE_ID = "workload"
SETTINGS_KEY = "main"


class WorkloadSettings(BaseModel):
    # None = first enabled Production SDP profile.
    sdp_profile_id: uuid.UUID | None = None
    # First sync reads requests updated within this window (covers the 1-year statistics).
    lookback_days: int = Field(400, ge=30, le=1100)
    # Only requests of these SDP support groups (empty = every group).
    group_names: list[str] = Field(default_factory=list)
    # SDP status names (case-insensitive) that mean the task is finished / dropped.
    done_statuses: list[str] = Field(default_factory=lambda: ["Resolved", "Closed", "Completed"])
    cancelled_statuses: list[str] = Field(default_factory=lambda: ["Canceled", "Cancelled", "Rejected"])
