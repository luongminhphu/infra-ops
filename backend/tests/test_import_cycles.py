"""Guards the core import graph: no cycles (even through function-level imports), permissions stays a leaf."""

from __future__ import annotations

import ast
from pathlib import Path

import app.core as core_pkg
from app.core.core_manifest import CORE_MANIFEST
from app.core.modules.registry import build_registry

CORE_DIR = Path(core_pkg.__file__).parent
# Connectors self-register: `registry` imports each connector module and each one imports `registry` back.
# This is intentional and local to the connectors package.
ALLOWED_CLUSTER_PREFIX = "app.core.connectors."


def _modules() -> dict[str, tuple[Path, bool]]:
    mods: dict[str, tuple[Path, bool]] = {}
    for p in CORE_DIR.rglob("*.py"):
        parts = p.relative_to(CORE_DIR.parent.parent).with_suffix("").parts
        is_pkg = parts[-1] == "__init__"
        mods[".".join(parts[:-1] if is_pkg else parts)] = (p, is_pkg)
    return mods


def _imports(tree: ast.AST):
    """Every import node, except the ones under `if TYPE_CHECKING:`."""
    if isinstance(tree, ast.If) and ast.unparse(tree.test) in ("TYPE_CHECKING", "typing.TYPE_CHECKING"):
        for n in tree.orelse:
            yield from _imports(n)
        return
    if isinstance(tree, ast.Import | ast.ImportFrom):
        yield tree
    for child in ast.iter_child_nodes(tree):
        yield from _imports(child)


def _graph() -> dict[str, set[str]]:
    mods = _modules()
    graph: dict[str, set[str]] = {}
    for name, (path, is_pkg) in mods.items():
        deps: set[str] = set()
        for n in _imports(ast.parse(path.read_text())):
            if isinstance(n, ast.Import):
                targets = [a.name for a in n.names]
            else:
                base = n.module or ""
                if n.level:
                    parts = name.split(".") if is_pkg else name.split(".")[:-1]
                    parts = parts[: len(parts) - (n.level - 1)]
                    base = ".".join([*parts, *([n.module] if n.module else [])])
                targets = [base, *(f"{base}.{a.name}" for a in n.names)]
            deps.update(t for t in targets if t in mods)
        deps.discard(name)
        graph[name] = deps
    return graph


def _sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    out: list[list[str]] = []

    def visit(v: str) -> None:
        index[v] = low[v] = len(index)
        stack.append(v)
        on_stack.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    for v in graph:
        if v not in index:
            visit(v)
    return out


def test_core_has_no_import_cycles() -> None:
    cycles = [
        c
        for c in _sccs(_graph())
        if not ("app.core.connectors.registry" in c and all(m.startswith(ALLOWED_CLUSTER_PREFIX) for m in c))
    ]
    assert cycles == []


def test_permissions_is_a_leaf() -> None:
    assert _graph()["app.core.permissions"] == set()
    assert not [n for n in _imports(ast.parse((CORE_DIR / "permissions.py").read_text()))]


def test_build_registry_with_core_manifest() -> None:
    registry = build_registry(CORE_MANIFEST, [])
    assert registry.get("core") is CORE_MANIFEST
