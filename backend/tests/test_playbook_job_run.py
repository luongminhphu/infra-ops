from __future__ import annotations

from typing import Any

import pytest

from app.core.modules.manifest import FactValue
from app.modules.playbook.run import run_for_job
from app.modules.playbook.templates import TEMPLATES
from app.modules.playbook.tree import Tree


def tpl(key: str) -> Tree:
    return Tree.model_validate(TEMPLATES[key]["tree"])


async def run(tree: Tree, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "load": None, "choices": {}, "decisions": {}, "marks": {}, "job_steps": {}, "workflow": "", "labels": {},
    }  # fmt: skip
    return await run_for_job(tree, **(args | kw))


def ids(r: dict[str, Any]) -> list[str]:
    return [e["id"] for e in r["trace"]]


class Fact:
    def __init__(self, v: str | None) -> None:
        self.v, self.calls = v, 0

    async def __call__(self, fact: str, param: str) -> FactValue | None:
        self.calls += 1
        return None if self.v is None else FactValue(value=self.v, source="test")


async def test_offboard_path_and_marks() -> None:
    t = tpl("offboard")
    choices = {"complete": 0, "collect": 0}
    r = await run(t, choices=choices, load=Fact("1"))
    assert r["status"] == "outcome" and r["path_complete"]
    assert "missing" not in ids(r) and "to_store" in ids(r)
    manual = {e["id"] for e in r["trace"] if e["manual"]}
    assert manual == {"receive", "reconcile", "review"}  # has_laptop is true: recheck_assets is skipped
    assert r["manual_left"] == len(manual)
    # a mark on the other branch is off-path
    r2 = await run(t, choices=choices, load=Fact("1"), marks={"missing": {"by": "x"}, "receive": {"by": "x"}})
    assert r2["off_path_marks"] == ["missing"]
    assert r2["manual_left"] == len(manual) - 1
    r3 = await run(t, choices=choices | {"collect": 1}, load=Fact("1"))
    assert "missing" in ids(r3) and r3["status"] == "outcome"
    assert next(e for e in r3["trace"] if e["id"] == "collect")["job_state"] == "not_applicable"


@pytest.mark.parametrize("key", ["onboard", "offboard", "transfer"])
async def test_ask_hr_loop_needs_choice(key: str) -> None:
    t = tpl(key)
    r = await run(t, choices={"complete": 1})
    assert r["status"] == "needs_choice"
    assert ids(r)[-3:] == ["complete", "ask_hr", "complete"]
    last = r["trace"][-1]
    assert "chosen" not in last and [o["label"] for o in last["options"]] == ["Đủ", "Thiếu / sai"]
    assert r["manual_left"] >= 1 and "ask_hr" in ids(r)
    assert (await run(t, choices={"complete": 1}, marks={"ask_hr": {"by": "x"}}))["manual_left"] == r[
        "manual_left"
    ] - 1
    r2 = await run(t, choices={"complete": 0}, marks={"ask_hr": {"by": "x"}}, load=Fact("1"))
    assert "ask_hr" in r2["off_path_marks"]


async def test_check_decisions() -> None:
    t = tpl("offboard")
    loader = Fact("1")
    r = await run(t, choices={"complete": 0}, load=loader)
    d = r["new_decisions"]["has_laptop"]
    assert d["branch"] == 0 and d["result"] is True and set(d) == {"branch", "left", "right", "op", "result"}
    # frozen decision: loader is never called, values shown
    frozen = {"branch": 1, "left": {"value": "0"}, "right": {"value": "1"}, "op": ">=", "result": False,
              "frozen_at": "2026-01-01"}  # fmt: skip

    async def boom(fact: str, param: str) -> FactValue | None:
        raise AssertionError("loader must not be called")

    r2 = await run(t, choices={"complete": 0}, load=boom, decisions={"has_laptop": frozen})
    e = next(e for e in r2["trace"] if e["id"] == "has_laptop")
    assert e["frozen_at"] == "2026-01-01" and e["result"] is False and "recheck_assets" in ids(r2)
    assert r2["new_decisions"] == {}
    # missing fact / no loader
    for load in (Fact(None), None):
        r3 = await run(t, choices={"complete": 0}, load=load)
        assert r3["status"] == "missing_data" and r3["new_decisions"] == {}


async def test_job_state() -> None:
    t = tpl("offboard")
    steps = {"biostar_offboard": {"status": "done", "title": "Khoá BioStar"}}
    r = await run(t, choices={"complete": 0}, workflow="lifecycle.offboard", job_steps=steps)
    by = {e["id"]: e for e in r["trace"]}
    assert by["biostar"]["job_state"] == "done" and by["biostar"]["job_title"] == "Khoá BioStar"
    assert by["account_disable"]["job_state"] == "not_applicable"
    r2 = await run(t, choices={"complete": 0}, workflow="lifecycle.onboard", job_steps=steps)
    assert r2["trace"][4]["job_state"] == "not_applicable"
    assert not any(e["manual"] for e in r["trace"] if e["kind"] == "job")


async def test_loop_without_choice() -> None:
    t = Tree.model_validate({"root": "a", "nodes": {
        "a": {"title": "A", "children": [{"to": "b"}]},
        "b": {"title": "B", "children": [{"to": "a"}]},
    }})  # fmt: skip
    r = await run(t)
    assert r["status"] == "loop" and ids(r) == ["a", "b"]


async def test_empty_label_option() -> None:
    r = await run(tpl("transfer"), choices={"complete": 0})
    assert r["status"] == "needs_choice"
    plan = r["trace"][-1]
    assert plan["id"] == "transfer_plan" and not plan["manual"]
    assert [o["label"] for o in plan["options"]] == ["Nhánh 1", "Tắt Microsoft"]


async def test_loop_entered_at_a_plain_step_asks_again_at_the_choice_inside() -> None:
    from app.modules.playbook.run import run_for_job
    from app.modules.playbook.tree import Tree

    tree = Tree.model_validate(
        {
            "root": "a",
            "nodes": {
                "a": {"title": "A", "children": [{"label": "", "to": "b"}]},
                "b": {"title": "B", "children": [{"label": "Lại", "to": "a"}, {"label": "Xong", "to": "c"}]},
                "c": {"title": "C"},
            },
        }
    )
    out = await run_for_job(
        tree, None, choices={"b": 0}, decisions={}, marks={}, job_steps={}, workflow="w", labels={}
    )
    assert out["status"] == "needs_choice"
    assert [e["id"] for e in out["trace"]] == ["a", "b", "b"]  # the revisited step is not repeated
    assert "chosen" not in out["trace"][-1] and out["trace"][-1]["options"]
