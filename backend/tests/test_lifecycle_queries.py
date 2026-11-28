"""Requests list (SQL filter/paging, accent-free search) and the assignee picker (one query)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.auth.models import User
from app.core.db import get_sessionmaker
from app.core.rbac.service import grant_role
from app.modules.lifecycle.models import LifecycleRequest


async def test_requests_list_search_filter_and_paging(admin) -> None:  # type: ignore[no-untyped-def]
    base = datetime(2030, 1, 1, tzinfo=UTC)
    rows = [
        ("q1", "Nguyễn Văn Zq", "onboard", []),
        ("q2", "Trần Thị Bình", "offboard", ["x"]),
        ("q3", "Đặng Văn Cường", None, []),
        ("q4", "NGUYỄN Thị 100%", "onboard", []),
    ]
    async with get_sessionmaker()() as s:
        for i, (rid, name, kind, errs) in enumerate(rows):
            s.add(
                LifecycleRequest(
                    id=rid, display_id=f"QQ-{i}", subject="Subj", full_name=name, kind=kind,
                    parse_errors=errs, created_at=base + timedelta(days=i),
                )
            )  # fmt: skip
        await s.commit()

    def ids(r) -> list[str]:  # type: ignore[no-untyped-def]
        return [x["id"] for x in r["items"]]

    r = (await admin.get("/api/lifecycle/requests", params={"q": "nguyen van zq"})).json()
    assert ids(r) == ["q1"] and r["total"] == 1
    r = (
        await admin.get("/api/lifecycle/requests", params={"q": "nguyen", "kind": "onboard", "limit": 1})
    ).json()
    assert ids(r) == ["q4"] and r["total"] >= 2
    assert ids((await admin.get("/api/lifecycle/requests", params={"q": "dang van"})).json()) == ["q3"]
    assert ids((await admin.get("/api/lifecycle/requests", params={"q": "QQ-1"})).json()) == ["q2"]
    assert ids((await admin.get("/api/lifecycle/requests", params={"q": "100%"})).json()) == ["q4"]
    assert (await admin.get("/api/lifecycle/requests", params={"q": "%"})).json()["total"] == 1  # literal %
    r = (await admin.get("/api/lifecycle/requests", params={"q": "qq-", "kind": "unknown"})).json()
    assert ids(r) == ["q3"]
    r = (await admin.get("/api/lifecycle/requests", params={"q": "qq-", "errors_only": True})).json()
    assert ids(r) == ["q2"]
    r = (await admin.get("/api/lifecycle/requests", params={"q": "qq-", "limit": 2, "offset": 1})).json()
    assert ids(r) == ["q3", "q2"] and r["total"] == 4
    assert r["kinds"]["onboard"] >= 2 and r["kinds"]["unknown"] >= 1 and r["with_errors"] >= 1


async def test_assignees_by_role(admin) -> None:  # type: ignore[no-untyped-def]
    async with get_sessionmaker()() as s:
        for upn, role, active in [
            ("as-op@test.local", "operator", True),
            ("as-viewer@test.local", "viewer", True),
            ("as-off@test.local", "operator", False),
            ("as-admin@test.local", "admin", True),
        ]:
            u = User(upn=upn, display_name=upn, is_active=active)
            s.add(u)
            await s.flush()
            await grant_role(s, u.id, role)
        await s.commit()
    got = {u["upn"] for u in (await admin.get("/api/lifecycle/assignees")).json()}
    assert {"as-op@test.local", "as-admin@test.local"} <= got
    assert not got & {"as-viewer@test.local", "as-off@test.local"}
