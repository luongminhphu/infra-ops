"""Discovers and holds the enabled modules.

Modules live in ``app.modules.<id>`` and expose ``manifest`` from ``manifest.py``.
The core itself is registered as the pseudo-module ``core``.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from typing import Any

from app.core.modules.manifest import DocumentKind, ModuleManifest, NavItem, Permission, ScheduledTask
from app.core.permissions import CORE_MANIFEST


class ModuleRegistry:
    def __init__(self, manifests: Iterable[ModuleManifest]) -> None:
        self._modules: dict[str, ModuleManifest] = {}
        for manifest in manifests:
            if manifest.id in self._modules:
                raise ValueError(f"Duplicate module id {manifest.id!r}")
            self._modules[manifest.id] = manifest
        self._permissions: dict[str, tuple[str, Permission]] = {}
        for manifest in self._modules.values():
            for perm in manifest.permissions:
                if perm.code in self._permissions:
                    raise ValueError(f"Duplicate permission {perm.code!r}")
                self._permissions[perm.code] = (manifest.id, perm)

    @property
    def modules(self) -> list[ModuleManifest]:
        return list(self._modules.values())

    def get(self, module_id: str) -> ModuleManifest | None:
        return self._modules.get(module_id)

    def permissions(self) -> list[tuple[str, Permission]]:
        return list(self._permissions.values())

    def scheduled_tasks(self) -> list[tuple[str, ScheduledTask]]:
        return [(m.id, t) for m in self._modules.values() for t in m.scheduled_tasks]

    def document_kinds(self) -> list[tuple[str, DocumentKind]]:
        return [(m.id, k) for m in self._modules.values() for k in m.document_kinds]

    def document_kind(self, key: str) -> tuple[str, DocumentKind] | None:
        return next(((mid, k) for mid, k in self.document_kinds() if k.key == key), None)

    def workflow(self, key: str) -> tuple[str, Any] | None:
        return next(((m.id, w) for m in self._modules.values() for w in m.workflows if w.key == key), None)

    def workflows(self) -> list[tuple[str, Any]]:
        return [(m.id, w) for m in self._modules.values() for w in m.workflows]

    def has_permission(self, code: str) -> bool:
        return code in self._permissions

    def nav_for(self, granted: set[str]) -> list[dict[str, object]]:
        groups: list[dict[str, object]] = []
        for manifest in self._modules.values():
            items: list[NavItem] = [item for item in manifest.nav if item.permission in granted]
            if items:
                groups.append(
                    {
                        "module": manifest.id,
                        "label": manifest.name,
                        "items": [
                            {"label": i.label, "path": i.path, "icon": i.icon, "permission": i.permission}
                            for i in items
                        ],
                    }
                )
        return groups

    def modules_using(self, system_type: str) -> list[ModuleManifest]:
        return [m for m in self._modules.values() if system_type in m.connectors_used]


def load_manifest(module_id: str) -> ModuleManifest:
    if not module_id.isidentifier():
        raise ValueError(f"Invalid module id {module_id!r}")
    mod = importlib.import_module(f"app.modules.{module_id}.manifest")
    manifest = getattr(mod, "manifest", None)
    if not isinstance(manifest, ModuleManifest):
        raise TypeError(f"app.modules.{module_id}.manifest must define `manifest: ModuleManifest`")
    if manifest.id != module_id:
        raise ValueError(f"Module directory {module_id!r} declares id {manifest.id!r}")
    return manifest


def build_registry(enabled: Iterable[str]) -> ModuleRegistry:
    return ModuleRegistry([CORE_MANIFEST, *(load_manifest(m) for m in enabled)])


_registry: ModuleRegistry | None = None


def set_registry(registry: ModuleRegistry) -> None:
    global _registry
    _registry = registry


def get_registry() -> ModuleRegistry:
    if _registry is None:
        raise RuntimeError("Module registry not initialised")
    return _registry
