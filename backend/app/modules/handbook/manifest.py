"""Infra Playbook: rules and how-to guides for IT Support, drawn as branching mind maps."""

from __future__ import annotations

from app.core.modules.manifest import ModuleManifest, NavItem, Permission
from app.modules.handbook.api import EDIT, PUBLISH, VIEW, router

manifest = ModuleManifest(
    id="handbook",
    name="Infra Playbook",
    description="Quy định và hướng dẫn xử lý cho IT Support, dạng sơ đồ rẽ nhánh.",
    permissions=[
        Permission(
            VIEW, "Xem Infra Playbook (bản đã xuất bản)", ("manager", "supervisor", "operator", "viewer")
        ),
        Permission(EDIT, "Soạn hướng dẫn (bản nháp, ảnh minh hoạ)", ("manager", "supervisor")),
        Permission(PUBLISH, "Xuất bản và xoá hướng dẫn", ("manager",)),
    ],
    routers=[router],
    nav=[NavItem("Infra Playbook", "/handbook", "book-open", VIEW)],
    migrations_branch="handbook",
)
