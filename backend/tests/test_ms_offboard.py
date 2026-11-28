"""Offboard account_disable / mailbox_handover / ad_move_ban / rtr (fake AD + Graph), plus Microsoft-enabled
end-to-end jobs (onboard and offboard) through the real job-creation path."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import respx
from sqlalchemy import select

from app.core.audit.models import AuditLog
from app.core.db import get_sessionmaker
from app.core.timeutil import today_vn
from app.core.workflow.models import Job, JobStep
from app.modules.lifecycle import microsoft as ms
from app.modules.lifecycle.models import LifecycleRequest
from app.modules.lifecycle.workflows import OFFBOARD
from tests.test_ms_onboard import BASE, GUID, FakeAd, FakeEx, FakeGraph, cfg, entra
from tests.test_workflows import job, run_worker, settle, step, systems, verify  # noqa: F401  (fixture)

BAN = f"OU=Ban_Users,{BASE}"
RETAINED = f"OU=Retained,{BASE}"
DN = f"CN=Phạm Thị Bình,OU=Sales,{BASE}"


def acct(**kw: Any) -> dict:
    return {
        "dn": DN,
        "sAMAccountName": "binh.pt",
        "userPrincipalName": "binh.pt@corp.vn",
        "displayName": "Phạm Thị Bình",
        "employeeID": "002100",
        "userAccountControl": 512,
        "adminCount": None,
        "memberOf": [f"CN=Sales,{BASE}"],
        **kw,
    }


class OffAd(FakeAd):
    async def disable(self, dn):  # type: ignore[no-untyped-def]
        self.calls.append(("disable", dn))
        for u in self.users:
            if u["dn"] == dn:
                u["userAccountControl"] |= 2

    async def move(self, dn, ou, new_rdn=None):  # type: ignore[no-untyped-def]
        self.calls.append(("move", dn, ou) if new_rdn is None else ("move", dn, ou, new_rdn))
        for u in self.users:
            if u["dn"] == dn:
                u["dn"] = f"{new_rdn or dn.split(',', 1)[0]},{ou}"


class OffGraph(FakeGraph):
    def __init__(self, ad: OffAd) -> None:
        super().__init__()
        self.ad, self.deleted = ad, False
        self.synced = False  # Entra Connect has pushed the disabled state

    async def get_user(self, uid):  # type: ignore[no-untyped-def]
        assert uid == GUID
        return {**self.users[0], "accountEnabled": not self.synced}

    async def revoke_sessions(self, uid):  # type: ignore[no-untyped-def]
        assert uid == GUID
        self.calls.append(("revoke", [c[0] for c in self.ad.calls]))

    async def find_user_by_employee_id(self, emp):  # type: ignore[no-untyped-def]
        return [] if self.deleted else await super().find_user_by_employee_id(emp)

    async def find_user_by_upn(self, upn):  # type: ignore[no-untyped-def]
        return next((u for u in self.users if u.get("userPrincipalName") == upn and not self.deleted), None)

    async def deleted_user(self, uid):  # type: ignore[no-untyped-def]
        assert uid == GUID
        return {"id": uid} if self.deleted else None


def install(monkeypatch, e: SimpleNamespace) -> None:  # type: ignore[no-untyped-def]
    async def load(_s):  # type: ignore[no-untyped-def]
        return e.settings

    def wrap(attr: str):  # type: ignore[no-untyped-def]
        @asynccontextmanager
        async def cm(_ctx, _purpose):  # type: ignore[no-untyped-def]
            yield getattr(e, attr)

        return cm

    for name, attr in (("ad", "ad"), ("exchange", "ex"), ("graph", "graph")):
        monkeypatch.setattr(ms, name, wrap(attr))
    monkeypatch.setattr(ms, "load_module_settings", load)


@pytest.fixture
def env(monkeypatch):  # type: ignore[no-untyped-def]
    ad = OffAd()
    ad.users = [acct()]
    g = OffGraph(ad)
    g.users = [entra(employeeId="002100", userPrincipalName="binh.pt@corp.vn", accountEnabled=True)]
    e = SimpleNamespace(ad=ad, ex=FakeEx(), graph=g, settings=cfg(ad_ban_ou=BAN))
    install(monkeypatch, e)
    return e


EMP = {"id": "002100", "full_name": "Phạm Thị Bình"}


def ctx(options: dict | None = None, outputs: dict | None = None, emp: dict | None = None):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        session=None,
        params={"employee": emp or EMP, "microsoft": True, "kind": "offboard", "options": options or {}},
        step=SimpleNamespace(output={}),
        input={},
        outputs=outputs or {},
    )


def test_offboard_order_and_flags() -> None:
    assert [s.key for s in OFFBOARD.steps] == [
        "check_request", "biostar_offboard", "find_sdp_user", "account_disable", "mailbox_handover",
        "ad_move_ban", "collect_assets", "return_assets", "return_doc", "rtr_checklist", "close_request",
    ]  # fmt: skip
    s = {x.key: x for x in OFFBOARD.steps}
    for k in ("account_disable", "mailbox_handover", "ad_move_ban", "rtr_checklist"):
        assert s[k].when({"microsoft": True}) and not s[k].when({}) and not s[k].when({"microsoft": False})
    assert s["account_disable"].confirm and s["account_disable"].writes == ("m365", "ad")
    assert s["ad_move_ban"].confirm and s["ad_move_ban"].writes == ("ad",) and not s["ad_move_ban"].skippable
    assert s["account_disable"].wait_timeout_seconds == s["ad_move_ban"].wait_timeout_seconds == 3 * 3600
    assert s["mailbox_handover"].kind == "manual" and s["mailbox_handover"].skippable
    assert s["mailbox_handover"].evidence_required and s["biostar_offboard"].confirm
    assert OFFBOARD.required_for_close == ("return_doc",)


async def test_disable_order_revoke_disable_wait_revoke(env) -> None:  # type: ignore[no-untyped-def]
    w = await ms.account_disable(ctx())
    assert w.status == "waiting" and w.retry_in_seconds == 300
    assert w.before_state == {
        "dn": DN,
        "ou": f"OU=Sales,{BASE}",
        "uac": 512,
        "memberOf": [f"CN=Sales,{BASE}"],
        "entra_id": GUID,
    }
    # AD is disabled first (independent of Graph); the best-effort revoke comes after it
    assert env.graph.calls == [("revoke", ["disable"])] and [c[0] for c in env.ad.calls] == ["disable"]
    env.graph.synced = True
    done = await ms.account_disable(ctx())
    assert done.status == "done" and done.output == {
        "dn": DN,
        "sam": "binh.pt",
        "upn": "binh.pt@corp.vn",
        "entra_id": GUID,
        "entra_checked": True,
    }
    assert len(env.graph.calls) == 2 and env.graph.calls[1] == (
        "revoke",
        ["disable"],
    )  # revoke again, no 2nd disable
    assert [c[0] for c in env.ad.calls] == ["disable"]


async def test_disable_already_disabled_and_no_entra_user(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.users[0]["userAccountControl"] = 514
    env.graph.users = []
    out = await ms.account_disable(ctx())
    assert out.status == "done" and out.output["entra_id"] is None and out.output["entra_checked"] is False
    assert "Không thấy user trên Entra" in out.message
    assert env.ad.calls == [] and env.graph.calls == []


@pytest.mark.parametrize(
    ("users", "needle"),
    [
        ([], "hiện có 0"),
        ([acct(), acct(dn=f"CN=Khác,{BASE}", sAMAccountName="khac")], "hiện có 2"),
        ([acct(adminCount=1)], "adminCount"),
        ([acct(dn="CN=Phạm Thị Bình,OU=Other,DC=corp,DC=local")], "ngoài OU gốc"),
        ([acct(displayName="Trần Văn Khác")], "không khớp"),
    ],
)
async def test_guard_refuses_both_steps(env, users, needle) -> None:  # type: ignore[no-untyped-def]
    env.ad.users = users
    for fn in (ms.account_disable, ms.ad_move_ban):
        out = await fn(ctx())
        assert out.status == "failed" and needle in out.message
    assert env.ad.calls == [] and env.graph.calls == []


async def test_guard_name_ignores_accents_and_order(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.users = [acct(displayName="BINH PHAM THI")]
    assert (await ms.account_disable(ctx())).status == "waiting"


async def test_move_requires_ban_ou_and_waits_for_deletion(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.users[0]["userAccountControl"] = 514
    env.settings = cfg(ad_ban_ou="")
    out = await ms.ad_move_ban(ctx())
    assert out.status == "failed" and "Ban_Users" in out.message and env.ad.calls == []
    env.settings = cfg(ad_ban_ou=BAN)
    env.graph.users[0]["assignedLicenses"] = [{"skuId": "sku-1"}]
    w = await ms.ad_move_ban(ctx())
    assert w.status == "waiting" and w.retry_in_seconds == 600
    assert w.before_state == {"dn": DN, "ou": f"OU=Sales,{BASE}", "licenses": ["sku-1"], "entra_id": GUID}
    assert env.ad.calls == [("move", DN, BAN)]
    env.ad.calls.clear()
    assert (
        await ms.ad_move_ban(ctx())
    ).status == "waiting" and env.ad.calls == []  # already moved: not again
    env.graph.deleted = True
    done = await ms.ad_move_ban(ctx())
    assert done.status == "done" and done.output["ou"] == BAN
    assert done.output["dn"] == f"CN=Phạm Thị Bình,{BAN}" and env.ad.calls == []


async def test_move_done_when_entra_user_gone_or_missing(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.users[0]["userAccountControl"] = 514
    env.graph.users = []
    out = await ms.ad_move_ban(ctx())
    assert out.status == "done" and env.ad.calls == [("move", DN, BAN)]
    assert out.output["entra_checked"] is False and "Không thấy user trên Entra" in out.message


async def test_move_refuses_an_enabled_account(env) -> None:  # type: ignore[no-untyped-def]
    out = await ms.ad_move_ban(ctx())
    assert out.status == "failed" and "chưa bị vô hiệu" in out.message and env.ad.calls == []


async def test_move_renames_on_duplicate_cn(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.users[0]["userAccountControl"] = 514
    env.ad.users.append(acct(dn=f"CN=Phạm Thị Bình,{BAN}", employeeID="009999", sAMAccountName="other"))
    out = await ms.ad_move_ban(ctx())
    new = "CN=Phạm Thị Bình (002100)"
    assert env.ad.calls == [("move", DN, BAN, new)]
    assert out.before_state["rdn"] == "CN=Phạm Thị Bình" and out.before_state["dn"] == DN
    env.ad.users.append(acct(dn=f"{new},{BAN}", employeeID="009998", sAMAccountName="third"))
    env.ad.users[0]["dn"] = DN
    env.ad.calls.clear()
    again = await ms.ad_move_ban(ctx())
    assert again.status == "failed" and "xử lý thủ công" in again.message and env.ad.calls == []


async def test_retained_ou_keeps_mailbox_and_schedules_release(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import date

    env.ad.users[0]["userAccountControl"] = 514
    env.settings = cfg(ad_ban_ou=BAN, ad_retained_ou=RETAINED)
    seen: list = []

    async def sched(_ctx, s, dn, upn):  # type: ignore[no-untyped-def]
        seen.append((s.mailbox_retention_days, dn, upn))
        return date(2027, 3, 1)

    monkeypatch.setattr(ms, "schedule_release", sched)
    out = await ms.ad_move_ban(ctx({"mailbox_handover": True}))
    assert out.status == "done" and env.ad.calls == [("move", DN, RETAINED)]  # no wait for deletion
    assert seen == [(90, f"CN=Phạm Thị Bình,{RETAINED}", "binh.pt@corp.vn")]
    assert "01/03/2027" in out.message and out.output["ou"] == RETAINED
    # the release job runs the same move in final mode: straight to Ban_Users, then waits for the deletion
    final = await ms.ad_move_ban_final(ctx({"mailbox_handover": True}))
    assert final.status == "waiting" and env.ad.calls[-1][2] == BAN and len(seen) == 1


async def test_retained_requested_but_not_configured_fails_before_touching_ad(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.users[0]["userAccountControl"] = 514
    out = await ms.ad_move_ban(ctx({"mailbox_handover": True}))
    assert out.status == "failed" and "ad_retained_ou" in out.message and env.ad.calls == []


async def test_entra_found_by_upn_and_upn_mismatch_refused(env) -> None:  # type: ignore[no-untyped-def]
    env.graph.users[0]["employeeId"] = None  # no employeeId on Entra: found by the AD UPN instead
    env.graph.synced = True
    out = await ms.account_disable(ctx())
    assert out.status == "done" and out.output["entra_id"] == GUID
    env.graph.users[0]["userPrincipalName"] = "other@corp.vn"
    env.graph.users[0]["employeeId"] = "002100"
    bad = await ms.account_disable(ctx())
    assert bad.status == "failed" and "khác UPN" in bad.message and bad.message.startswith("AD đã vô hiệu")


async def test_graph_failure_after_ad_disable_waits_instead_of_blocking(env) -> None:  # type: ignore[no-untyped-def]
    async def boom(_emp):  # type: ignore[no-untyped-def]
        raise RuntimeError("graph down")

    env.graph.find_user_by_employee_id = boom
    out = await ms.account_disable(ctx())
    assert out.status == "waiting" and out.message.startswith("AD đã vô hiệu, chưa thu hồi phiên Entra")
    assert env.ad.users[0]["userAccountControl"] & 2 and out.before_state["uac"] == 512


async def test_previews_show_the_real_account_and_ban_warning(env) -> None:  # type: ignore[no-untyped-def]
    lines = await ms.preview_ad_move_ban(ctx())
    text = "\n".join(lines)
    assert "binh.pt (binh.pt@corp.vn)" in text and "OU=Sales" in text and "1 nhóm" in text
    assert (
        "Chuyển vào Ban_Users: Entra sẽ xoá user, mailbox và license (khôi phục được trong 30 ngày)" in text
    )
    env.ad.users = []
    assert "Không thể thao tác" in (await ms.preview_account_disable(ctx()))[0]


async def test_skipped_when_microsoft_disabled(env) -> None:  # type: ignore[no-untyped-def]
    env.settings = cfg(microsoft_enabled=False)
    for fn in (ms.account_disable, ms.ad_move_ban):
        assert (await fn(ctx())).status == "skipped"
    assert env.ad.calls == [] and env.graph.calls == []


async def test_mailbox_handover_follows_the_option(env) -> None:  # type: ignore[no-untyped-def]
    off = await ms.prepare_mailbox_handover(ctx())
    assert "không yêu cầu" in off.message.lower()
    on = await ms.prepare_mailbox_handover(ctx({"mailbox_handover": True}))
    assert "Set-RemoteMailbox" in on.message and "Set-Mailbox" in on.message
    assert "không yêu cầu" in (await ms.run_mailbox_handover(ctx())).message.lower()
    assert (await ms.run_mailbox_handover(ctx({"mailbox_handover": True}))).message == "Đã chuyển mailbox."


async def test_offboard_rtr_summary() -> None:
    outputs = {
        "account_disable": {"sam": "binh.pt", "upn": "binh.pt@corp.vn"},
        "ad_move_ban": {"dn": "x"},
        "biostar_offboard": {"action": "disable"},
        "return_assets": {"returned_ids": ["5002"]},
        "return_doc": {"number": "BBTH-001"},
    }
    c = ctx(outputs=outputs)
    text = (await ms.prepare_rtr_checklist(c)).output["summary"]
    assert text == (await ms.run_rtr_checklist(c)).output["summary"]
    assert text.startswith("Offboard – Phạm Thị Bình (Mã NV 002100)")
    for part in ("binh.pt;", "UPN: binh.pt@corp.vn", "Ban_Users", "BioStar: disable", "BBTH-001", "kho: 1"):
        assert part in text


# ------------------------------------------------------------------ Microsoft-enabled end to end
@pytest.fixture
async def ms_on(admin, systems, monkeypatch):  # type: ignore[no-untyped-def]  # noqa: F811
    """Real settings with Microsoft on (so jobs get params.microsoft), fake AD / Exchange / Graph."""
    keys = ("display_id", "subject", "employee_id", "full_name", "kind", "effective_date", "fields")
    async with get_sessionmaker()() as s:
        rows = {r: await s.get(LifecycleRequest, r) for r in ("9101", "9102")}
        saved = {r: {k: getattr(row, k) for k in keys} for r, row in rows.items()}
    before = (await admin.get("/api/lifecycle/settings")).json()["values"]
    ms_values = {
        "microsoft_enabled": True,
        "upn_domain": "corp.vn",
        "ad_base_ou": BASE,
        "ad_default_ou": f"OU=Sales,{BASE}",
        "ad_ban_ou": BAN,
        "ad_retained_ou": RETAINED,
        "mailbox_retention_days": 90,
        "remote_routing_domain": "corp.mail.onmicrosoft.com",
    }
    r = await admin.put("/api/lifecycle/settings", json={**before, **ms_values})
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as s:  # saving re-parses the (empty) descriptions: put the rows back
        for rid, vals in saved.items():
            row = await s.get(LifecycleRequest, rid)
            for k, v in vals.items():
                setattr(row, k, v)
            row.parse_errors = []
        await s.commit()
    ad = OffAd()
    g = OffGraph(ad)
    e = SimpleNamespace(
        ad=ad,
        ex=FakeEx(),
        graph=g,
        settings=cfg(ad_ban_ou=BAN, ad_retained_ou=RETAINED, biostar_profile_id=before["biostar_profile_id"]),
    )  # the faked settings must name the same BioStar as the saved ones: unset means "first enabled"
    install(monkeypatch, e)
    yield e
    await admin.put("/api/lifecycle/settings", json=before)
    async with get_sessionmaker()() as s:
        for j in await s.scalars(select(Job).where(Job.external_ref.in_(["9101", "9102"]))):
            await s.delete(j)  # other tests expect their own fresh (non-Microsoft) jobs
        for rid, vals in saved.items():
            row = await s.get(LifecycleRequest, rid)
            for k, v in vals.items():
                setattr(row, k, v)
            row.parse_errors = []
        await s.commit()


async def fresh_job(admin, request_id: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    async with get_sessionmaker()() as s:
        for j in await s.scalars(select(Job).where(Job.external_ref == request_id)):
            await s.delete(j)
        await s.commit()
    r = await admin.post("/api/lifecycle/jobs", json={"request_id": request_id})
    assert r.status_code == 201, r.text
    return r.json()


async def due(job_id: str, key: str) -> None:
    async with get_sessionmaker()() as s:
        st = await s.scalar(select(JobStep).where(JobStep.key == key, JobStep.job_id == job_id))
        st.next_run_at = None
        await s.commit()


async def confirm(admin, j: dict, key: str, typed: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    r = await admin.post(f"/api/core/jobs/{j['id']}/steps/{key}/confirm", json={"typed": typed})
    assert r.status_code == 200, r.text
    return await settle(admin, j["id"])


async def sign(admin, j: dict, key: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    doc_id = step(j, key)["output"]["document_id"]
    await admin.post(f"/api/core/documents/{doc_id}/finalize")
    await admin.post(
        f"/api/core/documents/{doc_id}/signed",
        files={"file": ("s.pdf", b"%PDF-1.4\n%signed\n", "application/pdf")},
    )
    await due(j["id"], key)
    return await settle(admin, j["id"])


def left(j: dict) -> list:  # type: ignore[type-arg]
    return [(s["key"], s["status"], s["message"]) for s in j["steps"] if s["status"] != "done"]


@respx.mock(assert_all_called=False)
async def test_offboard_end_to_end_with_microsoft(admin, systems, ms_on) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    e = ms_on
    e.ad.users = [acct()]
    e.graph.users = [entra(employeeId="002100", userPrincipalName="binh.pt@corp.vn", accountEnabled=True)]
    j = await fresh_job(admin, "9102")
    assert [s["key"] for s in j["steps"]] == [s.key for s in OFFBOARD.steps]
    j = (await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})).json()
    j = await settle(admin, j["id"])
    assert (
        step(j, "biostar_offboard")["status"] == "waiting" and e.ad.calls == []
    )  # door first, confirm first
    j = await confirm(admin, j, "biostar_offboard", "002100")
    assert step(j, "account_disable")["status"] == "waiting" and e.ad.calls == []  # confirm first

    j = await confirm(admin, j, "account_disable", "002100")
    st = step(j, "account_disable")
    assert st["status"] == "waiting" and "Entra" in st["message"] and e.ad.users[0]["userAccountControl"] & 2
    e.graph.synced = True
    await due(j["id"], "account_disable")
    j = await settle(admin, j["id"])
    assert step(j, "account_disable")["status"] == "done"
    assert step(j, "mailbox_handover")["status"] == "waiting"  # manual; no mailbox requested
    r = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/mailbox_handover/skip", json={"reason": "Không có yêu cầu"}
    )
    assert r.status_code == 200, r.text
    j = await settle(admin, j["id"])
    assert step(j, "ad_move_ban")["status"] == "waiting"
    j = await confirm(admin, j, "ad_move_ban", "002100")
    assert step(j, "ad_move_ban")["status"] == "waiting" and e.ad.users[0]["dn"].endswith(BAN)
    e.graph.deleted = True
    await due(j["id"], "ad_move_ban")
    j = await settle(admin, j["id"])
    assert step(j, "ad_move_ban")["status"] == "done"
    assert step(j, "ad_move_ban")["before_state"]["dn"] == DN
    assert step(j, "collect_assets")["status"] == "waiting"
    await admin.post(
        f"/api/core/jobs/{j['id']}/steps/collect_assets/complete",
        json={"data": {"assets": [{"id": "5002", "condition": "good", "note": ""}]}},
    )
    j = await settle(admin, j["id"])
    j = await sign(admin, j, "return_doc")
    rtr = step(j, "rtr_checklist")
    assert rtr["status"] == "waiting" and "Ban_Users" in rtr["output"]["summary"]
    assert "binh.pt" in rtr["output"]["summary"] and "BioStar" in rtr["output"]["summary"]
    r = await admin.post(f"/api/core/jobs/{j['id']}/steps/rtr_checklist/complete", json={"data": {}})
    assert r.status_code == 200, r.text
    j = await settle(admin, j["id"])
    assert j["status"] == "completed", left(j)
    await assert_audit_has_no_password([j["id"]])
    async with (
        get_sessionmaker()() as s
    ):  # engine: audit "before" is the first-seen state, not the last run's
        row = await s.scalar(
            select(AuditLog).where(
                AuditLog.target_id == j["id"],
                AuditLog.action == "workflow.step_executed",
                AuditLog.target_label.like("%Vô hiệu tài khoản%"),
            )
        )
    assert row.before["uac"] == 512 and row.before["dn"] == DN


async def assert_audit_has_no_password(job_ids: list[str]) -> None:
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(AuditLog).where(AuditLog.target_id.in_(job_ids)))).all()
    assert rows
    for r in rows:
        blob = json.dumps([r.before, r.after, r.message, r.target_label], default=str).lower()
        assert "password" not in blob and "unicodepwd" not in blob, r.action


async def run_offboard_until_move(admin, e, request_id: str = "9102") -> dict:  # type: ignore[no-untyped-def,type-arg]
    j = await fresh_job(admin, request_id)
    j = (await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})).json()
    j = await settle(admin, j["id"])
    j = await confirm(admin, j, "biostar_offboard", "002100")
    j = await confirm(admin, j, "account_disable", "002100")
    e.graph.synced = True
    await due(j["id"], "account_disable")
    j = await settle(admin, j["id"])
    await admin.post(f"/api/core/jobs/{j['id']}/steps/mailbox_handover/skip", json={"reason": "test"})
    return await settle(admin, j["id"])


@respx.mock(assert_all_called=False)
async def test_offboard_without_mailbox_option_goes_straight_to_ban(admin, systems, ms_on) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    e = ms_on
    e.ad.users = [acct()]
    e.graph.users = [entra(employeeId="002100", userPrincipalName="binh.pt@corp.vn", accountEnabled=True)]
    j = await run_offboard_until_move(admin, e)
    j = await confirm(admin, j, "ad_move_ban", "002100")
    assert e.ad.users[0]["dn"].endswith(BAN) and step(j, "ad_move_ban")["status"] == "waiting"
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(Job).where(Job.workflow_key == "lifecycle.mailbox_release")) is None


@respx.mock(assert_all_called=False)
async def test_draft_params_mailbox_option(admin, systems, ms_on) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    off = await fresh_job(admin, "9102")
    r = await admin.put(
        f"/api/lifecycle/jobs/{off['id']}/params", json={"version": off["version"], "mailbox_handover": True}
    )
    assert r.status_code == 200 and r.json()["params"]["options"]["mailbox_handover"] is True
    async with get_sessionmaker()() as s:
        log = await s.scalar(
            select(AuditLog).where(
                AuditLog.target_id == off["id"], AuditLog.action == "workflow.job_params_updated"
            )
        )
        assert log.after["mailbox_handover"] is True and log.before["mailbox_handover"] is False
    on = await fresh_job(admin, "9101")
    r = await admin.put(
        f"/api/lifecycle/jobs/{on['id']}/params", json={"version": on["version"], "mailbox_handover": True}
    )
    assert r.status_code == 400 and "option_not_applicable" in r.text, r.text


@respx.mock(assert_all_called=False)
async def test_params_and_prepare_are_scoped_to_the_assignee(admin, client, systems, ms_on) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    roles = {r["code"]: r["id"] for r in (await admin.get("/api/core/roles")).json()}
    await admin.post(
        "/api/core/users",
        json={"upn": "op-scope@test.local", "display_name": "Op", "role_ids": [roles["operator"]]},
    )
    off = await fresh_job(admin, "9102")
    async with get_sessionmaker()() as s:
        row = await s.get(Job, off["id"])
        row.assignee_user_id, row.assignee_name = (
            uuid.UUID((await admin.get("/api/core/me")).json()["id"]),
            "A",
        )
        await s.commit()
    await client.login("op-scope@test.local")
    r = await client.put(
        f"/api/lifecycle/jobs/{off['id']}/params", json={"version": off["version"], "mailbox_handover": True}
    )
    assert r.status_code == 403, r.text
    assert (await client.get(f"/api/lifecycle/jobs/{off['id']}/prepare")).status_code == 403
    assert (await admin.get(f"/api/lifecycle/jobs/{off['id']}/prepare")).status_code == 200


@respx.mock(assert_all_called=False)
async def test_mailbox_option_needs_microsoft_on_the_job(admin, systems, ms_on) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    off = await fresh_job(admin, "9102")
    async with get_sessionmaker()() as s:
        row = await s.get(Job, off["id"])
        row.params = {**row.params, "microsoft": False}
        await s.commit()
    r = await admin.put(
        f"/api/lifecycle/jobs/{off['id']}/params", json={"version": off["version"], "mailbox_handover": True}
    )
    assert r.status_code == 400 and "option_not_applicable" in r.text, r.text


@respx.mock(assert_all_called=False)
async def test_mailbox_option_changes_on_a_running_job_until_ban_is_confirmed(admin, systems, ms_on) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    e = ms_on
    e.ad.users = [acct()]
    e.graph.users = [entra(employeeId="002100", userPrincipalName="binh.pt@corp.vn", accountEnabled=True)]
    j = await run_offboard_until_move(admin, e)
    assert j["status"] != "draft"
    r = await admin.put(
        f"/api/lifecycle/jobs/{j['id']}/params", json={"version": j["version"], "mailbox_handover": True}
    )
    assert r.status_code == 200 and r.json()["params"]["options"]["mailbox_handover"] is True, r.text
    j = await confirm(admin, j, "ad_move_ban", "002100")
    assert e.ad.users[0]["dn"] == f"CN=Phạm Thị Bình,{RETAINED}"  # the option set after start was honoured
    r = await admin.put(
        f"/api/lifecycle/jobs/{j['id']}/params", json={"version": j["version"], "mailbox_handover": False}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "option_locked", r.text
    r = await admin.put(
        f"/api/lifecycle/jobs/{j['id']}/params",
        json={"version": j["version"], "sdp_user_id": "1", "sdp_user_name": "x"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_not_draft", r.text


@respx.mock(assert_all_called=False)
async def test_offboard_keeps_mailbox_then_scheduled_release(admin, systems, ms_on) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    e = ms_on
    e.ad.users = [acct()]
    e.graph.users = [entra(employeeId="002100", userPrincipalName="binh.pt@corp.vn", accountEnabled=True)]
    j = await fresh_job(admin, "9102")
    async with get_sessionmaker()() as s:  # the draft option (a later task adds the UI for it)
        row = await s.get(Job, j["id"])
        row.params = {**row.params, "options": {"mailbox_handover": True}}
        row.assignee_name = "Kỹ thuật viên A"
        await s.commit()
    j = (await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})).json()
    j = await settle(admin, j["id"])
    j = await confirm(admin, j, "biostar_offboard", "002100")
    j = await confirm(admin, j, "account_disable", "002100")
    e.graph.synced = True
    await due(j["id"], "account_disable")
    j = await settle(admin, j["id"])
    await admin.post(f"/api/core/jobs/{j['id']}/steps/mailbox_handover/skip", json={"reason": "test"})
    j = await settle(admin, j["id"])
    j = await confirm(admin, j, "ad_move_ban", "002100")
    assert step(j, "ad_move_ban")["status"] == "done", left(j)  # no waiting for deletion
    assert e.ad.users[0]["dn"] == f"CN=Phạm Thị Bình,{RETAINED}"
    async with get_sessionmaker()() as s:
        rel = await s.scalar(select(Job).where(Job.workflow_key == "lifecycle.mailbox_release"))
        assert rel.idempotency_key == "lifecycle:mailbox_release:9102" and rel.status == "scheduled"
        assert rel.scheduled_for == today_vn() + timedelta(days=90)
        assert rel.assignee_name == "Kỹ thuật viên A" and rel.params["microsoft"] is True
        assert rel.params["dn"] == e.ad.users[0]["dn"] and rel.params["employee"]["id"] == "002100"
        rid = str(rel.id)
        rel.scheduled_for = today_vn()  # simulate the retention end date
        await s.commit()
    rj = await settle(admin, rid)
    assert rj["status"] == "waiting" and step(rj, "ad_move_ban")["status"] == "waiting"
    assert e.ad.users[0]["dn"].endswith(RETAINED)  # nothing moved before the confirmation
    rj = await confirm(admin, rj, "ad_move_ban", "002100")
    assert e.ad.users[0]["dn"] == f"CN=Phạm Thị Bình,{BAN}" and step(rj, "ad_move_ban")["status"] == "waiting"
    e.graph.deleted = True
    await due(rid, "ad_move_ban")
    rj = await settle(admin, rid)
    assert rj["status"] == "completed", left(rj)
    await assert_audit_has_no_password([j["id"], rid])


@respx.mock(assert_all_called=False)
async def test_onboard_end_to_end_with_microsoft(admin, systems, ms_on) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    e = ms_on
    e.graph.users = []  # nothing synced yet; the license step has no sku to wait for
    j = await fresh_job(admin, "9101")
    assert "account_plan" in [s["key"] for s in j["steps"]]
    j = (
        await admin.put(
            f"/api/lifecycle/jobs/{j['id']}/params",
            json={
                "version": j["version"],
                "assets": [{"id": "5001", "name": "HSC-LT-5001", "tag": "LT5001", "product_type": "Laptop"}],
            },
        )
    ).json()
    j = (await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})).json()
    j = await settle(admin, j["id"])
    plan = step(j, "account_plan")
    assert plan["status"] == "waiting" and plan["output"]["proposed_sam"]
    r = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/account_plan/complete",
        json={"data": {"sam": plan["output"]["proposed_sam"]}},
    )
    assert r.status_code == 200, r.text
    j = await settle(admin, j["id"])
    j = await confirm(admin, j, "ad_account", "003551")
    assert step(j, "ad_account")["status"] == "done" and e.ad.users[0]["employeeID"] == "003551"
    assert e.ex.enabled, step(j, "m365_license")
    assert step(j, "m365_license")["status"] == "done", step(j, "m365_license")
    assert step(j, "assign_assets")["status"] == "done"
    systems.bs_users["003551"]["fingerprint_template_count"] = "2"
    r = await admin.post(f"/api/core/jobs/{j['id']}/steps/biostar_enroll/complete", json={"data": {}})
    assert r.status_code == 200, r.text
    j = await settle(admin, j["id"])
    soft = step(j, "it_handover")["output"]["software"]
    r = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/it_handover/complete", json={"data": {"installed": soft}}
    )
    assert r.status_code == 200, r.text
    j = await settle(admin, j["id"])
    j = await sign(admin, j, "handover_doc")
    r = await admin.post(f"/api/core/jobs/{j['id']}/steps/rtr_checklist/complete", json={"data": {}})
    assert r.status_code == 200, r.text
    j = await settle(admin, j["id"])
    assert j["status"] == "completed", left(j)
    await assert_audit_has_no_password([j["id"]])
