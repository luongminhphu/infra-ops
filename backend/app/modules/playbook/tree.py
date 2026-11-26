"""A guide's map: steps (title + note) with labelled branches. A branch to a step that already hangs elsewhere is a
"jump" (e.g. back to an earlier check); the map is still drawn as a tree from the root."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_NODES = 300
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class Branch(BaseModel):
    label: str = Field("", max_length=100)  # the choice that leads there: "Có", "Không", "Máy Windows"…
    to: str = Field(min_length=1, max_length=40)


class Node(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    note: str = Field("", max_length=20_000)  # light markdown: **bold**, lists, [links](…), ![images](…)
    children: list[Branch] = Field(default_factory=list, max_length=20)


class Tree(BaseModel):
    root: str
    nodes: dict[str, Node] = Field(min_length=1, max_length=MAX_NODES)

    @field_validator("nodes")
    @classmethod
    def _ids(cls, v: dict[str, Node]) -> dict[str, Node]:
        bad = [k for k in v if not NODE_ID.match(k)]
        if bad:
            raise ValueError(f"Mã bước không hợp lệ: {', '.join(bad[:5])}")
        return v

    @model_validator(mode="after")
    def _links(self) -> Tree:
        if self.root not in self.nodes:
            raise ValueError("Bước gốc không tồn tại.")
        for nid, n in self.nodes.items():
            for b in n.children:
                if b.to not in self.nodes:
                    raise ValueError(f"Bước “{n.title}” rẽ tới bước không tồn tại.")
                if b.to == nid:
                    raise ValueError(f"Bước “{n.title}” không thể rẽ về chính nó.")
        return self


def unreachable(tree: Tree) -> list[str]:
    """Titles of steps no branch leads to from the root (invisible on the map: refused at publish)."""
    seen, stack = {tree.root}, [tree.root]
    while stack:
        for b in tree.nodes[stack.pop()].children:
            if b.to not in seen:
                seen.add(b.to)
                stack.append(b.to)
    return [n.title for k, n in tree.nodes.items() if k not in seen]


def starter(title: str) -> dict[str, Any]:
    """A new guide: one root step to grow from."""
    root = uuid.uuid4().hex[:8]
    return {"root": root, "nodes": {root: {"title": title, "note": "", "children": []}}}
