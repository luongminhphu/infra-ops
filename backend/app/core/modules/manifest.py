"""Contract every module implements to plug into the platform core."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

SystemRole = Literal["admin", "helpdesk", "viewer"]


@dataclass(frozen=True)
class Permission:
    """A permission code: ``<module>.<resource>.<action>``.

    ``default_roles`` are granted the permission the first time the platform sees it.
    Admins can later revoke it; the platform never re-grants automatically.
    """

    code: str
    description: str
    default_roles: tuple[SystemRole, ...] = ()


@dataclass(frozen=True)
class NavItem:
    """Sidebar entry. ``path`` is the frontend route, ``permission`` gates visibility."""

    label: str
    path: str
    icon: str
    permission: str


@dataclass(frozen=True)
class DashboardWidget:
    """A dashboard tile. ``load`` returns JSON data; ``kind`` tells the frontend how to render it."""

    key: str
    title: str
    kind: str
    permission: str
    load: Callable[[AsyncSession], Awaitable[dict[str, Any]]]
    order: int = 100


@dataclass(frozen=True)
class ScheduledTask:
    """Periodic background job run by the worker (``python -m app.worker``).

    ``handler`` receives a fresh session and returns a small JSON summary stored as the last result.
    ``key`` must be prefixed with the module id.
    """

    key: str
    description: str
    handler: Callable[[AsyncSession], Awaitable[dict[str, Any]]]
    interval_seconds: int = 1800
    timeout_seconds: int = 900
    initial_delay_seconds: int = 60
    enabled_by_default: bool = True


@dataclass(frozen=True)
class ModuleManifest:
    id: str
    name: str
    description: str = ""
    permissions: Sequence[Permission] = ()
    routers: Sequence[APIRouter] = ()
    nav: Sequence[NavItem] = ()
    # Connector system types the module relies on; shown as "used by" on connection profiles.
    connectors_used: Sequence[str] = ()
    # Alembic branch label owning this module's tables (None when the module has no tables).
    migrations_branch: str | None = None
    dashboard_widgets: Sequence[DashboardWidget] = ()
    scheduled_tasks: Sequence[ScheduledTask] = ()
    tags: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for perm in self.permissions:
            if not perm.code.startswith(f"{self.id}."):
                raise ValueError(f"Permission {perm.code!r} must be prefixed with module id {self.id!r}")
        for task in self.scheduled_tasks:
            if not task.key.startswith(f"{self.id}."):
                raise ValueError(f"Scheduled task {task.key!r} must be prefixed with module id {self.id!r}")
