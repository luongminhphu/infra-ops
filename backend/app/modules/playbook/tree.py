"""A guide's map: steps (title + note) with labelled branches. A branch to a step that already hangs elsewhere is a
"jump" (e.g. back to an earlier check); the map is still drawn as a tree from the root."""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_NODES = 300
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class Branch(BaseModel):
    label: str = Field("", max_length=100)  # the choice that leads there: "Có", "Không", "Máy Windows"…
    to: str = Field(min_length=1, max_length=40)


class Action(BaseModel):
    """A tool the step opens (a page of the app), by the key the frontend registers, e.g. "lifecycle.jobs"."""

    tool: str = Field(pattern=r"^[a-z0-9_.-]{1,64}$")
    label: str = Field("", max_length=60)


class Operand(BaseModel):
    """A fact about the employee (with its parameter, e.g. the product type) or a fixed value."""

    fact: str | None = Field(None, max_length=64)
    param: str = Field("", max_length=100)
    value: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def _one(self) -> Operand:
        if (self.fact is None) == (self.value is None):
            raise ValueError("Mỗi vế của điều kiện là một dữ liệu hoặc một giá trị.")
        return self


class Check(BaseModel):
    left: Operand
    op: Literal[">=", ">", "<=", "<", "==", "!="]
    right: Operand


class Outcome(BaseModel):
    result: Literal["standard", "adhoc", "exception"]
    approval: str = Field("", max_length=200)  # who approves / what happens next


class Node(BaseModel):
    # step: read and act; check: the app tests a condition (branch 1 = Đúng, branch 2 = Sai); outcome: classification
    kind: Literal["step", "check", "outcome"] = "step"
    title: str = Field(min_length=1, max_length=200)
    note: str = Field("", max_length=20_000)  # light markdown: **bold**, lists, [links](…), ![images](…)
    children: list[Branch] = Field(default_factory=list, max_length=20)
    actions: list[Action] = Field(default_factory=list, max_length=6)
    check: Check | None = None
    outcome: Outcome | None = None

    @model_validator(mode="after")
    def _kind(self) -> Node:
        if self.kind == "check":
            if self.check is None:
                raise ValueError(f"Bước kiểm tra “{self.title}” chưa có điều kiện.")
            if len(self.children) > 2:
                raise ValueError(f"Bước kiểm tra “{self.title}” chỉ có nhánh Đúng và Sai.")
        if self.kind == "outcome" and self.outcome is None:
            raise ValueError(f"Bước kết luận “{self.title}” chưa chọn kết luận.")
        return self


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
