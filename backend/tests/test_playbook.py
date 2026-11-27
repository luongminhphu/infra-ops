"""Infra Playbook: guides as branching maps, drafts, published versions, images."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.playbook.tree import Tree, unreachable

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def tree() -> dict:  # type: ignore[type-arg]
    return {
        "root": "start",
        "nodes": {
            "start": {"title": "Máy không lên mạng", "note": "Kiểm tra **cáp** trước.",
                      "children": [{"label": "Đèn cổng sáng", "to": "ip"}, {"label": "Đèn tắt", "to": "cable"}]},
            "ip": {"title": "Kiểm tra IP", "note": "", "children": [{"label": "Chưa được", "to": "cable"}]},
            "cable": {"title": "Thay cáp / báo Network", "note": "", "children": []},
        },
    }  # fmt: skip


def test_tree_rules() -> None:
    t = Tree.model_validate(tree())
    assert unreachable(t) == []  # "cable" is reached twice (a jump), still one step
    bad = tree()
    bad["nodes"]["ip"]["children"] = [{"label": "", "to": "nowhere"}]
    with pytest.raises(ValidationError):
        Tree.model_validate(bad)
    loop = tree()
    loop["nodes"]["ip"]["children"] = [{"label": "", "to": "ip"}]
    with pytest.raises(ValidationError):
        Tree.model_validate(loop)
    orphan = tree()
    orphan["nodes"]["lost"] = {"title": "Bước lạc", "note": "", "children": []}
    assert unreachable(Tree.model_validate(orphan)) == ["Bước lạc"]


async def test_guide_draft_publish_versions_and_readers(admin, client) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/playbook/guides", json={"title": "Xử lý mất mạng", "category": "Mạng"})
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    draft = (await admin.get(f"/api/playbook/guides/{gid}", params={"version": "draft"})).json()
    assert len(draft["tree"]["nodes"]) == 1  # starts with one root step

    body = {"title": "Xử lý mất mạng", "category": "Mạng", "description": "Máy trạm không vào mạng",
            "tree": tree(), "base_updated_at": draft["draft_updated_at"]}  # fmt: skip
    saved = await admin.put(f"/api/playbook/guides/{gid}/draft", json=body)
    assert saved.status_code == 200, saved.text
    stale = await admin.put(f"/api/playbook/guides/{gid}/draft", json=body)  # started from the old copy
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_draft"

    # A step nothing leads to is refused at publish.
    orphan = tree()
    orphan["nodes"]["lost"] = {"title": "Bước lạc", "note": "", "children": []}
    await admin.put(f"/api/playbook/guides/{gid}/draft",
                    json={**body, "tree": orphan, "base_updated_at": saved.json()["draft_updated_at"]})  # fmt: skip
    r = await admin.post(f"/api/playbook/guides/{gid}/publish", json={"note": "v1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unreachable_steps"
    current = (await admin.get(f"/api/playbook/guides/{gid}", params={"version": "draft"})).json()
    await admin.put(f"/api/playbook/guides/{gid}/draft",
                    json={**body, "base_updated_at": current["draft_updated_at"]})  # fmt: skip
    r = await admin.post(f"/api/playbook/guides/{gid}/publish", json={"note": "Bản đầu"})
    assert r.status_code == 201 and r.json()["published_version"] == 1

    # Images: PNG by its bytes only.
    img = await admin.post(f"/api/playbook/guides/{gid}/images", files={"file": ("a.png", PNG, "image/png")})
    assert img.status_code == 201, img.text
    assert (await admin.get(img.json()["url"])).content == PNG
    fake = await admin.post(
        f"/api/playbook/guides/{gid}/images", files={"file": ("a.png", b"<svg/>", "image/png")}
    )
    assert fake.json()["error"]["code"] == "unsupported_file"

    # An IT Support operator reads the published version only, and cannot edit.
    roles = {x["code"]: x["id"] for x in (await admin.get("/api/core/roles")).json()}
    await admin.post("/api/core/users", json={"upn": "hb-op@test.local", "display_name": "Op",
                                              "role_ids": [roles["operator"]]})  # fmt: skip
    await client.login("hb-op@test.local")
    listed = (await client.get("/api/playbook/guides")).json()
    assert [g["title"] for g in listed["items"]] == ["Xử lý mất mạng"] and not listed["can_edit"]
    pub = (await client.get(f"/api/playbook/guides/{gid}")).json()
    assert pub["version"] == 1 and pub["tree"]["nodes"]["start"]["children"][1]["to"] == "cable"
    assert (await client.get(f"/api/playbook/guides/{gid}", params={"version": "draft"})).status_code == 404
    assert (await client.post("/api/playbook/guides", json={"title": "x"})).status_code == 403
    assert (await client.get(img.json()["url"])).status_code == 200

    # Editing after publishing does not change what readers see until the next publish.
    current = (await admin.get(f"/api/playbook/guides/{gid}", params={"version": "draft"})).json()
    changed = tree()
    changed["nodes"]["start"]["title"] = "Mất mạng (sửa)"
    await admin.put(f"/api/playbook/guides/{gid}/draft",
                    json={**body, "tree": changed, "base_updated_at": current["draft_updated_at"]})  # fmt: skip
    assert (await client.get(f"/api/playbook/guides/{gid}")).json()["tree"]["nodes"]["start"][
        "title"
    ] == "Máy không lên mạng"
    await admin.post(f"/api/playbook/guides/{gid}/publish", json={"note": "Đổi tên"})
    hist = (await client.get(f"/api/playbook/guides/{gid}/versions")).json()
    assert [v["version"] for v in hist] == [2, 1] and hist[0]["note"] == "Đổi tên"
    assert (await client.get(f"/api/playbook/guides/{gid}", params={"version": "1"})).json()["version"] == 1

    assert (await client.delete(f"/api/playbook/guides/{gid}")).status_code == 403
    assert (await admin.delete(f"/api/playbook/guides/{gid}")).status_code == 204


def test_old_module_name_in_env_still_enables_playbook() -> None:
    from app.core.config import Settings

    assert Settings(enabled_modules="lifecycle,handbook,playbook").enabled_modules == [
        "lifecycle",
        "playbook",
    ]


MONITOR_GUIDE = {
    "root": "start",
    "nodes": {
        "start": {"title": "Yêu cầu cấp thêm màn hình", "children": [{"label": "", "to": "chk"}],
                  "actions": [{"tool": "lifecycle.reconciliation", "label": "Xem đối soát"}]},
        "chk": {"kind": "check", "title": "Còn trong tiêu chuẩn?",
                "check": {"left": {"fact": "lifecycle.assets_in_use", "param": "Monitor"}, "op": "<",
                          "right": {"fact": "lifecycle.standard_quantity", "param": "Monitor"}},
                "children": [{"label": "Đúng", "to": "std"}, {"label": "Sai", "to": "why"}]},
        "why": {"title": "Có lý do nghiệp vụ?", "children": [{"label": "Có", "to": "adhoc"}, {"label": "Không", "to": "exc"}]},
        "std": {"kind": "outcome", "title": "Cấp theo tiêu chuẩn", "outcome": {"result": "standard"}},
        "adhoc": {"kind": "outcome", "title": "Adhoc", "outcome": {"result": "adhoc", "approval": "Function Head duyệt"}},
        "exc": {"kind": "outcome", "title": "Exception", "outcome": {"result": "exception", "approval": "IT Manager duyệt"}},
    },
}  # fmt: skip


async def test_run_decides_checks_and_stops_for_choices() -> None:
    from app.core.modules.manifest import FactValue
    from app.modules.playbook.run import compare, run_tree

    t = Tree.model_validate(MONITOR_GUIDE)
    facts = {("lifecycle.assets_in_use", "Monitor"): FactValue(1, "SDP: 1 Monitor"),
             ("lifecycle.standard_quantity", "Monitor"): FactValue(1, "Tiêu chuẩn band B3: 1 Monitor")}  # fmt: skip

    async def load(k: str, p: str) -> FactValue | None:
        return facts.get((k, p))

    r = await run_tree(t, load, {}, {})
    assert r["status"] == "needs_choice" and [e["id"] for e in r["trace"]] == ["start", "chk", "why"]
    assert r["trace"][1]["result"] is False and r["trace"][1]["left"]["source"] == "SDP: 1 Monitor"
    r = await run_tree(t, load, {"why": 0}, {})
    assert r["status"] == "outcome" and r["outcome"]["result"] == "adhoc" and r["outcome"]["label"] == "Adhoc"

    async def nothing(k: str, p: str) -> FactValue | None:
        return None

    r = await run_tree(t, nothing, {}, {"lifecycle.assets_in_use": "Số tài sản đang dùng"})
    assert r["status"] == "missing_data" and r["trace"][-1]["missing"] == "Số tài sản đang dùng"
    assert compare("2", ">=", 1) and compare("B5", "==", "b5") and not compare("x", "!=", "X")
    bad = {**MONITOR_GUIDE, "nodes": {**MONITOR_GUIDE["nodes"], "chk": {"kind": "check", "title": "x"}}}
    with pytest.raises(ValidationError):
        Tree.model_validate(bad)


async def test_lifecycle_facts_and_run_endpoint(admin, client) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from app.core.db import get_sessionmaker
    from app.modules.lifecycle.models import LifecycleRequest, ReconRow, Snapshot, SyncRun

    async with get_sessionmaker()() as s:
        run = SyncRun(trigger="manual", status="success", finished_at=datetime(2099, 1, 1, tzinfo=UTC),
                      started_at=datetime(2099, 1, 1, tzinfo=UTC), stats={}, errors={})  # fmt: skip
        s.add(run)
        await s.flush()
        s.add(ReconRow(run_id=run.id, employee_id="008801", full_name="Lê Màn Hình", department="Trading",
                       data={"asset_ids": ["m1", "m2"], "cells": {"assets": {"status": "match"}}}, issue_count=0))  # fmt: skip
        for aid in ("m1", "m2"):
            s.add(Snapshot(run_id=run.id, system="sdp_asset", external_id=aid,
                           data={"id": aid, "name": "HP E24", "asset_tag": aid, "product_type": "Monitor",
                                 "state": "In Use"}))  # fmt: skip
        s.add(LifecycleRequest(id="pb-1", display_id="8801", subject="x", employee_id="008801", full_name="Lê Màn Hình",
                               kind="onboard", fields={"band": "B3"}, parse_errors=[], description_text=""))  # fmt: skip
        await s.commit()

    keys = {f["key"] for f in (await admin.get("/api/playbook/facts")).json()}
    assert {"lifecycle.assets_in_use", "lifecycle.standard_quantity", "lifecycle.band"} <= keys

    # A supervisor builds and publishes the guide (managers and supervisors design playbooks).
    roles = {x["code"]: x["id"] for x in (await admin.get("/api/core/roles")).json()}
    await admin.post("/api/core/users", json={"upn": "pb-sup@test.local", "display_name": "Sup",
                                              "role_ids": [roles["supervisor"]]})  # fmt: skip
    await client.login("pb-sup@test.local")
    gid = (await client.post("/api/playbook/guides", json={"title": "Cấp thêm màn hình"})).json()["id"]
    d = (await client.get(f"/api/playbook/guides/{gid}", params={"version": "draft"})).json()
    r = await client.put(f"/api/playbook/guides/{gid}/draft", json={"title": "Cấp thêm màn hình", "tree": MONITOR_GUIDE,
                                                                       "base_updated_at": d["draft_updated_at"]})  # fmt: skip
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/playbook/guides/{gid}/publish", json={})).status_code == 201

    res = (
        await client.post(
            f"/api/playbook/guides/{gid}/run", json={"employee_id": "8801", "choices": {"why": 1}}
        )
    ).json()
    chk = res["trace"][1]
    assert res["employee_id"] == "008801" and chk["left"]["value"] == 2 and chk["right"]["value"] == 1
    assert "band B3" in chk["right"]["source"] and chk["result"] is False
    assert res["status"] == "outcome" and res["outcome"]["result"] == "exception"
    unknown = (await client.post(f"/api/playbook/guides/{gid}/run", json={"employee_id": "999999"})).json()
    assert unknown["status"] == "missing_data"
    async with get_sessionmaker()() as s:  # the far-future run must not become other tests' "latest sync"
        await s.delete(await s.get(SyncRun, run.id))
        await s.commit()


async def test_permission_sync_tolerates_grants_moved_by_a_migration(admin) -> None:  # type: ignore[no-untyped-def]
    """A migration that renames a permission moves its grants; start-up must not insert them a second time."""
    from sqlalchemy import delete

    from app.core.db import get_sessionmaker
    from app.core.modules.registry import get_registry
    from app.core.rbac.models import KnownPermission
    from app.core.rbac.service import sync_permissions

    async with get_sessionmaker()() as s:
        await s.execute(delete(KnownPermission).where(KnownPermission.code == "playbook.guides.view"))
        await s.commit()
        assert await sync_permissions(s, get_registry()) == [
            "playbook.guides.view"
        ]  # grants already there: no error
        await s.commit()


async def test_onboard_template_is_a_valid_connected_guide(admin) -> None:  # type: ignore[no-untyped-def]
    from app.modules.playbook.templates import TEMPLATES

    for key, tpl in TEMPLATES.items():
        t = Tree.model_validate(tpl["tree"])
        assert unreachable(t) == [], key
        tools = {a.tool for n in t.nodes.values() for a in n.actions}
        assert tools <= {"lifecycle.requests", "lifecycle.reconciliation", "lifecycle.jobs", "lifecycle.stock",
                         "lifecycle.documents", "lifecycle.offboard-review", "workload.purchases",
                         "workload.tickets"}, (key, tools)  # fmt: skip
        assert any(n.kind == "outcome" for n in t.nodes.values()), key
        # Every check reads a fact the lifecycle module provides.
        facts = {o.fact for n in t.nodes.values() if n.check for o in (n.check.left, n.check.right) if o.fact}
        # HR gives no band: no template tests it (the allocation matrix works from the job title).
        assert facts <= {"lifecycle.assets_in_use", "lifecycle.standard_quantity"}, (
            key,
            facts,
        )
    listed = (await admin.get("/api/playbook/templates")).json()
    assert [t["key"] for t in listed] == [
        "onboard",
        "offboard",
        "extra_device",
        "recall_device",
        "replace_device",
    ]
    assert 15 < listed[0]["steps"] < 21
    r = await admin.post("/api/playbook/guides", json={"title": "Onboard (mẫu)", "template": "onboard"})
    assert r.status_code == 201, r.text
    d = (await admin.get(f"/api/playbook/guides/{r.json()['id']}", params={"version": "draft"})).json()
    assert d["category"] == "Vòng đời nhân sự" and d["tree"]["root"] == "receive"
    # Onboard always means a new employee ID and new accounts: no rehire / duplicate branch.
    assert "rehire" not in d["tree"]["nodes"] and d["tree"]["nodes"]["license_level"]["kind"] == "step"
    for key in ("offboard", "extra_device", "recall_device", "replace_device"):
        r = await admin.post("/api/playbook/guides", json={"title": f"{key} (mẫu)", "template": key})
        assert r.status_code == 201, r.text
    assert (
        await admin.post("/api/playbook/guides", json={"title": "x", "template": "nope"})
    ).status_code == 404


async def test_job_steps_link_playbook_and_jobs(admin, client) -> None:  # type: ignore[no-untyped-def]
    """A "Bước của Job" points at a workflow step (no copied checklist); publishing refuses a step that does not
    exist; a Job step finds the published guide that explains it."""
    from app.modules.playbook.templates import TEMPLATES

    for key in ("onboard", "offboard"):
        jobs = [n for n in TEMPLATES[key]["tree"]["nodes"].values() if n["kind"] == "job"]
        assert len(jobs) >= 3, key
    with pytest.raises(ValidationError):
        Tree.model_validate({"root": "a", "nodes": {"a": {"title": "A", "kind": "job"}}})

    r = await admin.post("/api/playbook/guides", json={"title": "Offboard (Job)", "template": "offboard"})
    gid = r.json()["id"]
    d = (await admin.get(f"/api/playbook/guides/{gid}", params={"version": "draft"})).json()
    bad = {**d["tree"], "nodes": {**d["tree"]["nodes"]}}
    bad["nodes"]["close"] = {
        **bad["nodes"]["close"],
        "job": {"workflow": "lifecycle.offboard", "step": "gone_step"},
    }
    saved = await admin.put(f"/api/playbook/guides/{gid}/draft", json={"title": d["title"], "tree": bad,
                                                                       "base_updated_at": d["draft_updated_at"]})  # fmt: skip
    assert saved.status_code == 200, saved.text
    r = await admin.post(f"/api/playbook/guides/{gid}/publish", json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_job_step", r.text
    await admin.put(f"/api/playbook/guides/{gid}/draft", json={"title": d["title"], "tree": d["tree"],
                                                               "base_updated_at": saved.json()["draft_updated_at"]})  # fmt: skip
    assert (await admin.post(f"/api/playbook/guides/{gid}/publish", json={})).status_code == 201

    found = await admin.get(
        "/api/playbook/step-guide", params={"workflow": "lifecycle.offboard", "step": "collect_assets"}
    )
    assert found.status_code == 200 and found.json()["node_id"] == "collect", found.text
    none = await admin.get(
        "/api/playbook/step-guide", params={"workflow": "lifecycle.offboard", "step": "nothing"}
    )
    assert none.status_code == 404

    # Lưu đồ quy trình is configuration: checklist managers see it, operators do not (they read the Job and Playbook).
    paths = lambda me: {i["path"] for g in me["nav"] for i in g["items"]}  # noqa: E731
    assert "/lifecycle/flows" in paths((await admin.get("/api/core/me")).json())
    roles = {x["code"]: x["id"] for x in (await admin.get("/api/core/roles")).json()}
    await admin.post("/api/core/users", json={"upn": "pb-flows-op@test.local", "display_name": "Op",
                                              "role_ids": [roles["operator"]]})  # fmt: skip
    await client.login("pb-flows-op@test.local")
    assert "/lifecycle/flows" not in paths((await client.get("/api/core/me")).json())
    # They still read the workflows a "Bước của Job" shows.
    assert any(w["key"] == "lifecycle.offboard" for w in (await client.get("/api/core/workflows")).json())


def test_replace_device_template_routes_approval_by_device_and_requester() -> None:
    """Green Policy: PC / Laptop need the Function Head unless they asked; other devices need IT's evidence only."""
    from app.modules.playbook.templates import REPLACE_DEVICE

    n = Tree.model_validate(REPLACE_DEVICE["tree"]).nodes
    to = lambda k: {b.label: b.to for b in n[k].children}  # noqa: E731
    assert to("kind") == {"PC / Laptop": "assess", "Thiết bị khác": "other_check"}
    assert to("assess")["Cần thay thế"] == "requester"
    assert to("requester") == {"Function Head tự đề xuất": "source", "Người khác đề xuất": "fh_approval"}
    assert to("fh_approval") == {"Đã duyệt": "source", "Không duyệt": "rejected"}
    assert to("other_check")["Hỏng – cần thay"] == "source"  # no approval step on this path
    assert to("source") == {"Kho có máy phù hợp": "swap", "Kho không có": "purchase"}
    assert n["rejected"].kind == n["done"].kind == n["repaired"].kind == "outcome"


async def test_switch_step_takes_the_branch_named_after_the_value() -> None:
    """ "Rẽ nhánh theo dữ liệu": any number of branches, one per value, "Khác" for the rest."""
    from app.core.modules.manifest import FactValue
    from app.modules.playbook.run import run_tree
    from app.modules.playbook.tree import unnamed_switch_branches

    def end(title: str) -> dict:  # type: ignore[type-arg]
        return {"kind": "outcome", "title": title, "outcome": {"result": "standard", "approval": ""}}

    tree = Tree.model_validate({
        "root": "lv",
        "nodes": {
            "lv": {"kind": "switch", "title": "Office Tools theo cấp bậc", "switch": {"fact": "lifecycle.level", "value": None},
                   "children": [{"label": "Lãnh đạo cấp cao", "to": "top"}, {"label": "Quản lý", "to": "mgr"},
                                {"label": "Nhân viên kỹ thuật", "to": "tech"}, {"label": "Khác", "to": "std"}]},
            "top": end("Bộ đầy đủ"), "mgr": end("Bộ quản lý"), "tech": end("Bộ kỹ thuật"), "std": end("Bộ chuẩn"),
        },
    })  # fmt: skip
    for level, outcome in (("quản lý", "mgr"), ("Nhân viên kỹ thuật", "tech"), ("Intern / Vendor", "std")):

        async def load(k: str, p: str, level: str = level) -> FactValue | None:
            return FactValue(level, "Request RTR #1: chức danh có …")

        r = await run_tree(tree, load, {}, {})
        assert r["status"] == "outcome" and r["trace"][-1]["id"] == outcome, level
        assert r["trace"][0]["left"]["value"] == level and r["trace"][0]["chosen"] is not None

    async def none(k: str, p: str) -> FactValue | None:
        return None

    assert (await run_tree(tree, none, {}, {}))["status"] == "missing_data"
    no_other = tree.model_copy(deep=True)
    no_other.nodes["lv"].children = no_other.nodes["lv"].children[:3]

    async def odd(k: str, p: str) -> FactValue | None:
        return FactValue("Thực tập", "x")

    assert (await run_tree(no_other, odd, {}, {}))["status"] == "no_branch"
    assert unnamed_switch_branches(tree) == []
    blank = tree.model_copy(deep=True)
    blank.nodes["lv"].children[0].label = " "
    assert unnamed_switch_branches(blank) == ["Office Tools theo cấp bậc"]
    with pytest.raises(ValidationError):  # a switch needs a fact
        Tree.model_validate({"root": "a", "nodes": {"a": {"kind": "switch", "title": "A"}}})
