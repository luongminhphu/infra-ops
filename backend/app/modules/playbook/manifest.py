"""Infra Playbook: rules and how-to guides for IT Support, drawn as branching mind maps."""

from __future__ import annotations

from app.core.modules.manifest import ModuleManifest, NavItem, Permission
from app.modules.playbook.api import EDIT, PUBLISH, VIEW, router

manifest = ModuleManifest(
    id="playbook",
    name="Infra Playbook",
    description="Quy định và hướng dẫn xử lý cho IT Support, dạng sơ đồ rẽ nhánh.",
    permissions=[
        Permission(
            VIEW, "Xem Infra Playbook (bản đã xuất bản)", ("manager", "supervisor", "operator", "viewer")
        ),
        Permission(
            EDIT,
            "Soạn Playbook: thêm, sửa, thay thế bước (bản nháp, ảnh minh hoạ)",
            ("manager", "supervisor"),
        ),
        Permission(PUBLISH, "Xuất bản và xoá Playbook", ("manager", "supervisor")),
    ],
    routers=[router],
    nav=[NavItem("Infra Playbook", "/playbook", "book-open", VIEW)],
    migrations_branch="playbook",
)
