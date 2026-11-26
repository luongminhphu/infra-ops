"""Infra Playbook: guides as branching maps, drafts, published versions, images."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.handbook.tree import Tree, unreachable

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
    r = await admin.post("/api/handbook/guides", json={"title": "Xử lý mất mạng", "category": "Mạng"})
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    draft = (await admin.get(f"/api/handbook/guides/{gid}", params={"version": "draft"})).json()
    assert len(draft["tree"]["nodes"]) == 1  # starts with one root step

    body = {"title": "Xử lý mất mạng", "category": "Mạng", "description": "Máy trạm không vào mạng",
            "tree": tree(), "base_updated_at": draft["draft_updated_at"]}  # fmt: skip
    saved = await admin.put(f"/api/handbook/guides/{gid}/draft", json=body)
    assert saved.status_code == 200, saved.text
    stale = await admin.put(f"/api/handbook/guides/{gid}/draft", json=body)  # started from the old copy
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_draft"

    # A step nothing leads to is refused at publish.
    orphan = tree()
    orphan["nodes"]["lost"] = {"title": "Bước lạc", "note": "", "children": []}
    await admin.put(f"/api/handbook/guides/{gid}/draft",
                    json={**body, "tree": orphan, "base_updated_at": saved.json()["draft_updated_at"]})  # fmt: skip
    r = await admin.post(f"/api/handbook/guides/{gid}/publish", json={"note": "v1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unreachable_steps"
    current = (await admin.get(f"/api/handbook/guides/{gid}", params={"version": "draft"})).json()
    await admin.put(f"/api/handbook/guides/{gid}/draft",
                    json={**body, "base_updated_at": current["draft_updated_at"]})  # fmt: skip
    r = await admin.post(f"/api/handbook/guides/{gid}/publish", json={"note": "Bản đầu"})
    assert r.status_code == 201 and r.json()["published_version"] == 1

    # Images: PNG by its bytes only.
    img = await admin.post(f"/api/handbook/guides/{gid}/images", files={"file": ("a.png", PNG, "image/png")})
    assert img.status_code == 201, img.text
    assert (await admin.get(img.json()["url"])).content == PNG
    fake = await admin.post(
        f"/api/handbook/guides/{gid}/images", files={"file": ("a.png", b"<svg/>", "image/png")}
    )
    assert fake.json()["error"]["code"] == "unsupported_file"

    # An IT Support operator reads the published version only, and cannot edit.
    roles = {x["code"]: x["id"] for x in (await admin.get("/api/core/roles")).json()}
    await admin.post("/api/core/users", json={"upn": "hb-op@test.local", "display_name": "Op",
                                              "role_ids": [roles["operator"]]})  # fmt: skip
    await client.login("hb-op@test.local")
    listed = (await client.get("/api/handbook/guides")).json()
    assert [g["title"] for g in listed["items"]] == ["Xử lý mất mạng"] and not listed["can_edit"]
    pub = (await client.get(f"/api/handbook/guides/{gid}")).json()
    assert pub["version"] == 1 and pub["tree"]["nodes"]["start"]["children"][1]["to"] == "cable"
    assert (await client.get(f"/api/handbook/guides/{gid}", params={"version": "draft"})).status_code == 404
    assert (await client.post("/api/handbook/guides", json={"title": "x"})).status_code == 403
    assert (await client.get(img.json()["url"])).status_code == 200

    # Editing after publishing does not change what readers see until the next publish.
    current = (await admin.get(f"/api/handbook/guides/{gid}", params={"version": "draft"})).json()
    changed = tree()
    changed["nodes"]["start"]["title"] = "Mất mạng (sửa)"
    await admin.put(f"/api/handbook/guides/{gid}/draft",
                    json={**body, "tree": changed, "base_updated_at": current["draft_updated_at"]})  # fmt: skip
    assert (await client.get(f"/api/handbook/guides/{gid}")).json()["tree"]["nodes"]["start"][
        "title"
    ] == "Máy không lên mạng"
    await admin.post(f"/api/handbook/guides/{gid}/publish", json={"note": "Đổi tên"})
    hist = (await client.get(f"/api/handbook/guides/{gid}/versions")).json()
    assert [v["version"] for v in hist] == [2, 1] and hist[0]["note"] == "Đổi tên"
    assert (await client.get(f"/api/handbook/guides/{gid}", params={"version": "1"})).json()["version"] == 1

    assert (await client.delete(f"/api/handbook/guides/{gid}")).status_code == 403
    assert (await admin.delete(f"/api/handbook/guides/{gid}")).status_code == 204
