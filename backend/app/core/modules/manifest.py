"""Contract every module implements to plug into the platform core."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

SystemRole = Literal["admin", "manager", "supervisor", "operator", "viewer"]


@dataclass(frozen=True)
class Permission:
    """A permission code: ``<module>.<resource>.<action>``.

    ``default_roles`` are granted the permission the first time the platform sees it.
    Admins can later revoke it; the platform never re-grants automatically.
    """

    code: str
    description: str
    default_roles: tuple[SystemRole, ...] = ()


# Menu headings, in display order. Modules file each page under one by what it is for, so pages of
# different modules that do the same kind of work sit together. Report headings form the "Báo cáo" menu;
# the others the "Công cụ" menu.
NAV_WORK = "Xử lý công việc"
NAV_ASSETS = "Thiết bị & biên bản"
NAV_SOURCES = "Dữ liệu nguồn"
NAV_SETUP = "Quy trình & cấu hình"
NAV_STATS = "Thống kê"
NAV_REVIEWS = "Báo cáo rà soát"
NAV_GROUPS = (NAV_WORK, NAV_ASSETS, NAV_SOURCES, NAV_SETUP, NAV_STATS, NAV_REVIEWS)
NAV_REPORT_GROUPS = frozenset({NAV_STATS, NAV_REVIEWS})


@dataclass(frozen=True)
class NavItem:
    """Sidebar entry. ``path`` is the frontend route, ``permission`` gates visibility."""

    label: str
    path: str
    icon: str
    permission: str
    # Heading the item is listed under: one of NAV_GROUPS ("" = under the module name, in "Công cụ").
    group: str = ""


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
class NextActionSource:
    """Things the signed-in person should do next, for the "Việc tiếp theo" queue.

    ``load(session, user)`` returns items ``{key, kind, priority, title, detail, due, href, action}``:
    priority 0 = blocked on this person now, 1 = overdue / urgent, 2 = due soon, 3 = to know, 4 = suggestion.
    """

    key: str
    permission: str
    load: Callable[[AsyncSession, Any], Awaitable[list[dict[str, Any]]]]
    # Counts for the summary at the top of "Việc của tôi": ``{key, group, label, count, href}`` where
    # group is done (last 7 days) | late | soon (next 3 days) | open.
    summary: Callable[[AsyncSession, Any], Awaitable[list[dict[str, Any]]]] | None = None


@dataclass(frozen=True)
class SearchSource:
    """Records the global search (Ctrl+K) can find, declared by the module that owns them.

    ``search(session, user, q, limit)`` returns items ``{title, detail, href, kind}``. It must apply the same row
    scope as the entity's list endpoint and match only fields that list shows to this user. ``q`` is raw (use
    ``app.core.text.fold``). ``key`` is prefixed with the module id; ``label`` is the group heading.
    """

    key: str
    permission: str
    label: str
    search: Callable[[AsyncSession, Any, str, int], Awaitable[list[dict[str, Any]]]]


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
class PlaceholderDoc:
    name: str
    description: str
    example: str = ""


@dataclass(frozen=True)
class DocumentKind:
    """A printable record type rendered from a DOCX template (docxtpl / Jinja2).

    ``data_model`` validates what users enter; ``build_context`` turns it into template variables;
    ``subject`` returns (subject key, label, external reference) for listing and search.
    """

    key: str
    title: str
    prefix: str
    default_template: Path
    data_model: type[BaseModel]
    build_context: Callable[[Any, str], dict[str, Any]]
    subject: Callable[[Any], tuple[str | None, str, str | None]]
    placeholders: Sequence[PlaceholderDoc]
    sample_data: dict[str, Any]
    view_permission: str
    create_permission: str
    manage_permission: str
    # Row scope: holders see every record; others only their own or those of Jobs they may view. None = no scope.
    view_all_permission: str | None = None


@dataclass(frozen=True)
class FactValue:
    """One piece of data about an employee, with where it came from (shown in the Playbook trace)."""

    value: Any  # number or text
    source: str  # e.g. "SDP: 1 Monitor (HP E24 G5)"


@dataclass(frozen=True)
class FactSource:
    """Data a Playbook check can test, declared by the module that owns it.

    ``load(session, employee_id, param)`` returns None when the data is unknown (never a guess). ``param_label``
    names the parameter a check must give (e.g. the product type), or is None when the fact takes none.
    """

    key: str
    label: str
    kind: str  # "number" | "text"
    load: Callable[[AsyncSession, str, str], Awaitable[FactValue | None]]
    param_label: str | None = None
    param_hint: str = ""


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
    next_actions: Sequence[NextActionSource] = ()
    scheduled_tasks: Sequence[ScheduledTask] = ()
    document_kinds: Sequence[DocumentKind] = ()
    workflows: Sequence[Any] = ()  # WorkflowDefinition (app.core.workflow.definitions)
    facts: Sequence[FactSource] = ()
    search_sources: Sequence[SearchSource] = ()
    tags: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for perm in self.permissions:
            if not perm.code.startswith(f"{self.id}."):
                raise ValueError(f"Permission {perm.code!r} must be prefixed with module id {self.id!r}")
        for fact in self.facts:
            if not fact.key.startswith(f"{self.id}."):
                raise ValueError(f"Fact {fact.key!r} must be prefixed with module id {self.id!r}")
        for wf in self.workflows:
            if not wf.key.startswith(f"{self.id}."):
                raise ValueError(f"Workflow {wf.key!r} must be prefixed with module id {self.id!r}")
        for kind in self.document_kinds:
            if not kind.key.startswith(f"{self.id}."):
                raise ValueError(f"Document kind {kind.key!r} must be prefixed with module id {self.id!r}")
        for src in self.search_sources:
            if not src.key.startswith(f"{self.id}."):
                raise ValueError(f"Search source {src.key!r} must be prefixed with module id {self.id!r}")
        for task in self.scheduled_tasks:
            if not task.key.startswith(f"{self.id}."):
                raise ValueError(f"Scheduled task {task.key!r} must be prefixed with module id {self.id!r}")
