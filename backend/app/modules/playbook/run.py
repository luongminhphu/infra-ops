"""Run a guide for one employee: follow the map from the root, let the app decide at check steps (from the facts
modules declare), stop where a person must choose or where data is missing. Pure apart from the fact loader."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.modules.manifest import FactValue
from app.modules.playbook.tree import Operand, Tree

MAX_STEPS = 100
OTHER = "khác"  # a switch branch with this label takes every value no other branch names
Loader = Callable[[str, str], Awaitable[FactValue | None]]
OUTCOME_LABEL = {"standard": "Tiêu chuẩn", "adhoc": "Adhoc", "exception": "Exception"}


def _number(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def compare(left: Any, op: str, right: Any) -> bool:
    a, b = _number(left), _number(right)
    if a is not None and b is not None:
        return {">=": a >= b, ">": a > b, "<=": a <= b, "<": a < b, "==": a == b, "!=": a != b}[op]
    x, y = str(left).strip().casefold(), str(right).strip().casefold()
    if op in ("==", "!="):
        return (x == y) == (op == "==")
    return {">=": x >= y, ">": x > y, "<=": x <= y, "<": x < y}[op]


async def run_tree(
    tree: Tree, load: Loader, choices: dict[str, int], labels: dict[str, str]
) -> dict[str, Any]:
    cache: dict[tuple[str, str], FactValue | None] = {}

    async def value(o: Operand) -> dict[str, Any] | None:
        if o.fact is None:
            return {"label": "Giá trị", "value": o.value, "source": "Nhập trong Playbook"}
        key = (o.fact, o.param)
        if key not in cache:
            cache[key] = await load(o.fact, o.param)
        fv = cache[key]
        label = labels.get(o.fact, o.fact) + (f" ({o.param})" if o.param else "")
        return None if fv is None else {"label": label, "value": fv.value, "source": fv.source}

    trace: list[dict[str, Any]] = []
    node_id = tree.root
    for _ in range(MAX_STEPS):
        node = tree.nodes[node_id]
        entry: dict[str, Any] = {"id": node_id, "kind": node.kind, "title": node.title}
        trace.append(entry)
        if node.kind == "outcome" and node.outcome:
            entry["outcome"] = node.outcome.model_dump()
            return {"status": "outcome", "trace": trace, "outcome": node.outcome.model_dump()
                    | {"label": OUTCOME_LABEL[node.outcome.result]}}  # fmt: skip
        if node.kind == "check" and node.check:
            left, right = await value(node.check.left), await value(node.check.right)
            entry |= {"left": left, "op": node.check.op, "right": right}
            if left is None or right is None:
                missing = node.check.left if left is None else node.check.right
                entry["missing"] = labels.get(missing.fact or "", missing.fact or "")
                return {"status": "missing_data", "trace": trace}
            ok = compare(left["value"], node.check.op, right["value"])
            entry["result"] = ok
            idx = 0 if ok else 1
            if idx >= len(node.children):
                return {"status": "end", "trace": trace}
            node_id = node.children[idx].to
            continue
        if node.kind == "switch" and node.switch:
            got = await value(node.switch)
            entry["left"] = got
            if got is None:
                entry["missing"] = labels.get(node.switch.fact or "", node.switch.fact or "")
                return {"status": "missing_data", "trace": trace}
            idx = next((i for i, b in enumerate(node.children) if compare(got["value"], "==", b.label)), None)
            if idx is None:
                idx = next(
                    (i for i, b in enumerate(node.children) if b.label.strip().casefold() == OTHER), None
                )
            if idx is None:
                return {"status": "no_branch", "trace": trace}
            entry["chosen"] = idx
            node_id = node.children[idx].to
            continue
        if not node.children:
            return {"status": "end", "trace": trace}
        if len(node.children) == 1:
            node_id = node.children[0].to
            continue
        pick = choices.get(node_id)
        if pick is None or not 0 <= pick < len(node.children):
            entry["options"] = [
                {"index": i, "label": b.label, "to": b.to} for i, b in enumerate(node.children)
            ]
            return {"status": "needs_choice", "trace": trace}
        entry["chosen"] = pick
        node_id = node.children[pick].to
    return {"status": "loop", "trace": trace}
