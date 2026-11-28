"""Onboard account_plan / ad_account steps against fake AD / Exchange clients (no network)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.connectors.exchange import ExchangeNotFound
from app.core.errors import AppError
from app.modules.lifecycle import microsoft as ms
from app.modules.lifecycle.settings import LifecycleSettings, OuRule
from app.modules.lifecycle.workflows import ONBOARD

ASSIGN = "lifecycle.job.assign"
BASE = "OU=Users,DC=corp,DC=local"
EMP = {
    "id": "003551",
    "full_name": "Nguyễn Thanh Bình",
    "job_title": "Chuyên viên",
    "department": "Kinh Doanh",
    "department_only": "Kinh Doanh",
    "division": "",
    "unit": "",
    "manager_employee_id": "000100",
    "phone": "0901234567",
}


def cfg(**kw: Any) -> LifecycleSettings:
    base = {
        "microsoft_enabled": True,
        "upn_domain": "corp.vn",
        "ad_base_ou": BASE,
        "ad_default_ou": f"OU=Sales,{BASE}",
        "ad_ou_map": [OuRule(department="IT", ou=f"OU=IT,{BASE}")],
        "ad_ban_ou": f"OU=Ban_Users,{BASE}",
        "remote_routing_domain": "corp.mail.onmicrosoft.com",
    }
    return LifecycleSettings(**{**base, **kw})


class FakeAd:
    def __init__(self) -> None:
        self.users: list[dict] = []  # {dn, sAMAccountName, employeeID, userAccountControl}
        self.calls: list[tuple] = []
        self.taken: set[str] = set()
        self.cns: list[str] = []

    async def exists_any(self, sam, upn, mail):  # type: ignore[no-untyped-def]
        return ["CN=x"] if (sam in self.taken or any(u["sAMAccountName"] == sam for u in self.users)) else []

    async def find_users(self, eid):  # type: ignore[no-untyped-def]
        return [u for u in self.users if u.get("employeeID") == eid]

    async def get(self, dn):  # type: ignore[no-untyped-def]
        return next((u for u in self.users if u["dn"].lower() == dn.lower()), None)

    async def add_user(self, ou, cn, attrs):  # type: ignore[no-untyped-def]
        self.calls.append(("add", attrs))
        dn = f"CN={cn},{ou}"
        self.cns.append(cn)
        self.users.append({"dn": dn, **attrs})
        return dn

    async def set_password_random(self, dn):  # type: ignore[no-untyped-def]
        self.calls.append(("pwd", dn))

    async def modify(self, dn, changes):  # type: ignore[no-untyped-def]
        self.calls.append(("modify", changes))

    async def enable(self, dn):  # type: ignore[no-untyped-def]
        self.calls.append(("enable", dn))
        for u in self.users:
            if u["dn"] == dn:
                u["userAccountControl"] = 512


class FakeEx:
    def __init__(self) -> None:
        self.boxes: set[str] = set()
        self.enabled: list[tuple] = []
        self.not_found = False

    async def get_remote_mailbox(self, ident):  # type: ignore[no-untyped-def]
        return {"x": 1} if ident in self.boxes else None

    async def enable_remote_mailbox(self, ident, routing):  # type: ignore[no-untyped-def]
        if self.not_found:
            raise ExchangeNotFound("not found")
        self.enabled.append((ident, routing))


PLANNED_DN = f"CN=Nguyễn Thanh Bình,OU=Sales,{BASE}"
GUID = "11111111-2222-3333-4444-555555555555"
SKU_E3, SKU_F1 = "aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000002"


class FakeGraph:
    def __init__(self) -> None:
        self.users: list[dict] = []
        self.free: dict[str, int] = {SKU_E3: 5, SKU_F1: 5}
        self.taken: set[str] = set()
        self.calls: list[tuple] = []

    async def address_taken(self, v):  # type: ignore[no-untyped-def]
        return v in self.taken

    async def find_user_by_employee_id(self, emp):  # type: ignore[no-untyped-def]
        return [u for u in self.users if u.get("employeeId") == emp]

    async def get_user(self, uid):  # type: ignore[no-untyped-def]
        assert uid == GUID  # always the Entra object id
        return next(u for u in self.users if u["id"] == uid)

    async def subscribed_skus(self):  # type: ignore[no-untyped-def]
        return [{"skuId": k, "enabled": v, "consumed": 0} for k, v in self.free.items()]

    async def set_usage_location(self, uid, code):  # type: ignore[no-untyped-def]
        assert uid == GUID
        self.calls.append(("loc", code))
        self.users[0]["usageLocation"] = code

    async def assign_licenses(self, uid, skus):  # type: ignore[no-untyped-def]
        assert uid == GUID
        self.calls.append(("assign", list(skus)))
        self.users[0]["assignedLicenses"] += [{"skuId": k} for k in skus]


@pytest.fixture
def env(monkeypatch):  # type: ignore[no-untyped-def]
    e = SimpleNamespace(
        ad=FakeAd(), ex=FakeEx(), graph=FakeGraph(), graph_down=False, settings=cfg(), perms={ASSIGN}
    )

    async def perms(_session, _uid, _registry):  # type: ignore[no-untyped-def]
        return e.perms

    monkeypatch.setattr(ms, "permissions_for_user", perms)
    monkeypatch.setattr(ms, "get_registry", lambda: None)

    async def load(_session):  # type: ignore[no-untyped-def]
        return e.settings

    @asynccontextmanager
    async def ad(_ctx, _purpose):  # type: ignore[no-untyped-def]
        yield e.ad

    @asynccontextmanager
    async def ex(_ctx, _purpose):  # type: ignore[no-untyped-def]
        yield e.ex

    @asynccontextmanager
    async def gr(_ctx, _purpose):  # type: ignore[no-untyped-def]
        if e.graph_down:
            raise RuntimeError("graph down")
        yield e.graph

    monkeypatch.setattr(ms, "graph", gr)
    monkeypatch.setattr(ms, "load_module_settings", load)
    monkeypatch.setattr(ms, "ad", ad)
    monkeypatch.setattr(ms, "exchange", ex)
    return e


def ctx(
    output: dict | None = None,
    inp: dict | None = None,
    outputs: dict | None = None,
    emp: dict | None = None,
    waited: float = 0,
):  # type: ignore[no-untyped-def]
    since = datetime.now(UTC) - timedelta(seconds=waited) if waited else None
    return SimpleNamespace(
        session=None,
        actor=SimpleNamespace(user_id=uuid.uuid4()),
        params={"employee": emp or EMP, "microsoft": True},
        step=SimpleNamespace(output=output or {}, waiting_since=since),
        input=inp or {},
        outputs=outputs or {},
    )


async def plan(env, **inp):  # type: ignore[no-untyped-def]
    prep = (await ms.prepare_account_plan(ctx())).output
    return prep, await ms.run_account_plan(ctx(prep, inp or {"sam": prep["proposed_sam"]}))


def test_ms_steps_only_in_jobs_created_with_microsoft() -> None:
    for s in ONBOARD.steps:
        assert s.when({"microsoft": True}) is True
    off = [s.key for s in ONBOARD.steps if s.when({"microsoft": False})]
    assert "account_plan" not in off and "ad_account" not in off and "check_request" in off
    assert not [s for s in ONBOARD.steps if s.key in ("account_plan", "ad_account") and s.when({})]


def test_onboard_order_and_flags() -> None:
    keys = [s.key for s in ONBOARD.steps]
    assert keys == [
        "check_request", "account_plan", "ad_account", "m365_license", "find_sdp_user", "assign_assets",
        "biostar_user", "biostar_enroll", "it_handover", "handover_doc", "rtr_checklist", "close_request",
    ]  # fmt: skip
    s = {x.key: x for x in ONBOARD.steps}
    assert s["ad_account"].confirm and s["ad_account"].writes == ("ad", "exchange")
    assert s["account_plan"].kind == "manual" and not s["account_plan"].skippable


async def test_skipped_when_disabled(env) -> None:  # type: ignore[no-untyped-def]
    env.settings = cfg(microsoft_enabled=False)
    assert (await ms.prepare_account_plan(ctx())).status == "skipped"
    assert (await ms.ad_account(ctx())).status == "skipped"
    assert env.ad.calls == []


async def test_plan_picks_first_free_and_pins(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.taken = {"binh.nt"}
    env.ad.users = [{"dn": f"CN=m,{BASE}", "sAMAccountName": "mgr", "employeeID": "000100"}]
    prep, out = await plan(env)
    assert [c["free"] for c in prep["candidates"]][:2] == [False, True]
    assert prep["proposed_sam"] == prep["candidates"][1]["sam"] != "binh.nt"
    assert prep["manager_dn"] == f"CN=m,{BASE}" and prep["ou"] == f"OU=Sales,{BASE}"
    assert out.status == "done" and out.output["sam"] == prep["proposed_sam"]
    assert out.output["upn"] == f"{prep['proposed_sam']}@corp.vn" and not out.output["custom"]


async def test_plan_run_refusals(env) -> None:  # type: ignore[no-untyped-def]
    prep = (await ms.prepare_account_plan(ctx())).output
    good = prep["proposed_sam"]
    with pytest.raises(AppError):  # custom value with bad characters
        await ms.run_account_plan(ctx(prep, {"sam": "Bad Name!"}))
    with pytest.raises(AppError):  # OU outside the base
        await ms.run_account_plan(ctx(prep, {"sam": good, "ou": "OU=Evil,DC=other,DC=local"}))
    for sam in ("trailing.", "x" * 21):  # trailing dot / longer than sam_max_length
        with pytest.raises(AppError):
            await ms.run_account_plan(ctx(prep, {"sam": sam}))
    custom = await ms.run_account_plan(ctx(prep, {"sam": "an.custom", "ou": f"OU=IT,{BASE}"}))
    assert custom.output["custom"] and custom.output["ou"] == f"OU=IT,{BASE}"
    bad = (await ms.prepare_account_plan(ctx(emp={**EMP, "id": "A12"}))).output
    assert bad["employee_id_valid"] is False
    with pytest.raises(AppError, match="Mã NV không hợp lệ"):
        await ms.run_account_plan(ctx(bad, {"sam": good}))


async def pinned(env, **over):  # type: ignore[no-untyped-def]
    _, out = await plan(env)
    return ctx(outputs={"account_plan": {**out.output, **over}})


async def test_ad_account_creates_once_with_employee_id(env) -> None:  # type: ignore[no-untyped-def]
    c = await pinned(env)
    out = await ms.ad_account(c)
    assert out.status == "done" and out.output["employee_id"] == "003551" and out.output["mailbox_enabled"]
    add = next(a for k, a in env.ad.calls if k == "add")
    assert add["employeeID"] == "003551" and add["userAccountControl"] == 514
    assert [k for k, *_ in env.ad.calls] == ["add", "pwd", "modify", "enable"]
    dn = out.output["dn"]
    assert env.ex.enabled == [(dn, f"{out.output['sam']}@corp.mail.onmicrosoft.com")]  # DN, not sAM
    again = await ms.ad_account(c)  # retry: nothing new is created
    assert again.status == "done" and sum(k == "add" for k, *_ in env.ad.calls) == 1
    assert again.before_state["existing"] is True
    blob = repr((out, again, env.ad.calls)).lower()
    assert "password" not in blob and "unicodepwd" not in blob


async def test_ad_account_partial_only_enables_mailbox(env) -> None:  # type: ignore[no-untyped-def]
    c = await pinned(env)
    sam = c.outputs["account_plan"]["sam"]
    env.ad.users = [
        {"dn": PLANNED_DN, "sAMAccountName": sam, "employeeID": "003551", "userAccountControl": 512}
    ]
    out = await ms.ad_account(c)
    assert out.status == "done" and env.ad.calls == [] and len(env.ex.enabled) == 1
    env.ex.boxes.add(c.outputs["account_plan"]["upn"])
    env.ex.enabled.clear()
    assert (await ms.ad_account(c)).before_state["remote_mailbox"] is True and env.ex.enabled == []


async def test_ad_account_failures(env) -> None:  # type: ignore[no-untyped-def]
    c = await pinned(env)
    env.ad.users = [
        {"dn": "CN=o", "sAMAccountName": "someone.else", "employeeID": "003551", "userAccountControl": 512}
    ]
    out = await ms.ad_account(c)
    assert out.status == "failed" and "someone.else" in out.message
    env.ad.users.append({"dn": "CN=p", "sAMAccountName": "dup", "employeeID": "003551"})
    assert (await ms.ad_account(c)).status == "failed"
    env.ad.users = []
    env.ad.taken = {c.outputs["account_plan"]["sam"]}
    assert (await ms.ad_account(c)).status == "failed"
    assert env.ad.calls == [] and env.ex.enabled == []


async def test_cn_collision_pins_suffixed_cn(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.users = [
        {"dn": f"CN=Nguyễn Thanh Bình,OU=Sales,{BASE}", "sAMAccountName": "other", "employeeID": "9"}
    ]
    c = await pinned(env)
    assert c.outputs["account_plan"]["cn"] == "Nguyễn Thanh Bình (003551)"
    await ms.ad_account(c)
    assert env.ad.cns == ["Nguyễn Thanh Bình (003551)"]


async def test_disabled_partial_account_gets_password_before_enable(env) -> None:  # type: ignore[no-untyped-def]
    c = await pinned(env)
    sam = c.outputs["account_plan"]["sam"]
    dn = PLANNED_DN
    env.ad.users = [{"dn": dn, "sAMAccountName": sam, "employeeID": "003551", "userAccountControl": 514}]
    out = await ms.ad_account(c)
    assert out.status == "done" and out.before_state["uac"] == 514
    assert [k for k, *_ in env.ad.calls] == ["pwd", "modify", "enable"]
    assert env.ad.calls[1][1] == {"pwdLastSet": 0} and env.ex.enabled[0][0] == dn


async def test_plan_marks_entra_taken_and_warns_when_graph_down(env) -> None:  # type: ignore[no-untyped-def]
    first = (await ms.prepare_account_plan(ctx())).output["candidates"][0]
    env.graph.taken = {first["upn"]}
    prep = (await ms.prepare_account_plan(ctx())).output
    assert prep["candidates"][0]["free"] is False and prep["proposed_sam"] != first["sam"]
    env.graph_down = True
    down = (await ms.prepare_account_plan(ctx())).output
    assert down["candidates"][0]["free"] and any("Entra" in w for w in down["warnings"])


PLAN = {
    "employee_id": "003551",
    "upn": "binh.nt@corp.vn",
    "license_skus": [SKU_E3, SKU_F1],
    "license_names": ["E3", "F1"],
    "software": ["Office", "Teams"],
    "sam": "binh.nt",
}


EXO = {"service": "exchange", "capabilityStatus": "Enabled", "provisioningStatus": "Success"}


def entra(**kw: Any) -> dict:
    return {
        "id": GUID,
        "employeeId": "003551",
        "userPrincipalName": "binh.nt@corp.vn",
        "mail": None,
        "provisionedPlans": [],
        "usageLocation": None,
        "assignedLicenses": [],
        **kw,
    }


async def test_license_waits_assigns_and_is_idempotent(env) -> None:  # type: ignore[no-untyped-def]
    c = ctx(outputs={"account_plan": PLAN})
    w = await ms.m365_license(c)
    assert w.status == "waiting" and w.retry_in_seconds == 300 and "Entra Connect" in w.message
    env.graph.users = [entra(assignedLicenses=[{"skuId": SKU_E3.upper()}])]
    env.graph.free[SKU_F1] = 0
    out = await ms.m365_license(c)  # no free unit: waits, nothing written
    assert out.status == "waiting" and out.retry_in_seconds == 1800 and "F1" in out.message
    assert env.graph.calls == []
    env.graph.free[SKU_F1] = 1
    out = await ms.m365_license(c)  # usageLocation first, only the missing sku, then waits for mail
    assert [k for k, _ in env.graph.calls] == ["loc", "assign"] and env.graph.calls[1][1] == [SKU_F1]
    assert out.status == "waiting" and "mailbox" in out.message and out.before_state["usage_location"] is None
    env.graph.calls.clear()
    assert (await ms.m365_license(c)).status == "waiting" and env.graph.calls == []  # rerun only waits
    env.graph.users[0]["mail"] = "binh.nt@corp.vn"  # mail alone (synced from AD) is not a mailbox
    assert (await ms.m365_license(c)).status == "waiting"
    env.graph.users[0]["provisionedPlans"] = [EXO]
    done = await ms.m365_license(c)
    assert (
        done.status == "done" and done.output["mail"] == "binh.nt@corp.vn" and done.output["entra_id"] == GUID
    )
    assert env.graph.calls == []


async def test_license_skipped(env) -> None:  # type: ignore[no-untyped-def]
    assert (
        await ms.m365_license(ctx(outputs={"account_plan": {**PLAN, "license_skus": []}}))
    ).status == "skipped"
    env.settings = cfg(microsoft_enabled=False)
    assert (await ms.m365_license(ctx(outputs={"account_plan": PLAN}))).status == "skipped"


async def test_it_handover_requires_every_software() -> None:
    c = ctx(output={"software": ["Office", "Teams"]}, inp={"installed": ["Office"]})
    with pytest.raises(AppError, match="Teams"):
        await ms.run_it_handover(c)
    c.input = {"installed": ["Office"], "not_needed": [{"name": "Teams", "note": ""}]}
    with pytest.raises(AppError):
        await ms.run_it_handover(c)
    c.input = {"installed": ["Office"], "not_needed": [{"name": "Teams", "note": "dùng web"}]}
    out = await ms.run_it_handover(c)
    assert out.status == "done" and out.output["not_needed"] == [{"name": "Teams", "note": "dùng web"}]


async def test_rtr_summary_has_upn_mail_and_handover_number() -> None:
    outputs = {
        "account_plan": PLAN,
        "m365_license": {"upn": "binh.nt@corp.vn", "mail": "binh.nt@corp.vn"},
        "biostar_user": {"created": True},
        "handover_doc": {"number": "BBBG-001"},
        "assign_assets": {"assigned": [{"name": "LT1", "tag": "T1"}]},
    }
    text = (await ms.run_rtr_checklist(ctx(outputs=outputs))).output["summary"]
    assert "binh.nt@corp.vn" in text and "Email: binh.nt@corp.vn" in text and "BBBG-001" in text
    assert "003551" in text and "LT1 (T1)" in text and "E3, F1" in text


async def test_returning_employee_old_account_is_never_reenabled(env) -> None:  # type: ignore[no-untyped-def]
    c = await pinned(env)
    old = f"CN=Nguyễn Thanh Bình,OU=Ban_Users,{BASE}"
    env.ad.users = [
        {"dn": old, "sAMAccountName": "binh.nt", "employeeID": "003551", "userAccountControl": 514}
    ]
    out = await ms.ad_account(c)
    assert out.status == "failed" and old in out.message
    assert env.ad.calls == [] and env.ex.enabled == []
    warn = (await ms.prepare_account_plan(ctx())).output["warnings"]
    assert any("003551" in w and old in w for w in warn)


async def test_pinned_dn_mismatch_and_admin_count_fail(env) -> None:  # type: ignore[no-untyped-def]
    c = await pinned(env)
    sam = c.outputs["account_plan"]["sam"]
    row = {"sAMAccountName": sam, "employeeID": "003551", "userAccountControl": 514}
    env.ad.users = [{**row, "dn": f"CN=Nguyễn Thanh Bình,OU=Other,{BASE}"}]
    assert (await ms.ad_account(c)).status == "failed"
    env.ad.users = [{**row, "dn": PLANNED_DN, "adminCount": 1}]
    assert (await ms.ad_account(c)).status == "failed"
    assert env.ad.calls == [] and env.ex.enabled == []


async def test_pinned_ou_in_ban_or_outside_base_fails(env) -> None:  # type: ignore[no-untyped-def]
    for ou in (f"OU=Ban_Users,{BASE}", "OU=Evil,DC=other,DC=local"):
        c = await pinned(env, ou=ou)
        assert (await ms.ad_account(c)).status == "failed"
    assert env.ad.calls == []


async def test_entra_address_taken_fails_and_graph_down_waits(env) -> None:  # type: ignore[no-untyped-def]
    c = await pinned(env)
    env.graph.taken = {c.outputs["account_plan"]["upn"]}
    assert (await ms.ad_account(c)).status == "failed"
    env.graph.taken = set()
    env.graph_down = True
    out = await ms.ad_account(c)
    assert out.status == "waiting" and out.retry_in_seconds == 300
    assert env.ad.calls == [] and env.ex.enabled == []


async def test_remote_mailbox_not_found_waits_then_fails(env) -> None:  # type: ignore[no-untyped-def]
    c = await pinned(env)
    env.ex.not_found = True
    out = await ms.ad_account(c)
    assert out.status == "waiting" and out.retry_in_seconds == 60 and out.before_state is not None
    c.step.waiting_since = datetime.now(UTC) - timedelta(seconds=700)
    assert (await ms.ad_account(c)).status == "failed"


async def test_license_upn_mismatch_and_unknown_sku_fail_without_writes(env) -> None:  # type: ignore[no-untyped-def]
    c = ctx(outputs={"account_plan": PLAN})
    env.graph.users = [entra(userPrincipalName="someone.else@corp.vn")]
    out = await ms.m365_license(c)
    assert out.status == "failed" and "someone.else@corp.vn" in out.message and env.graph.calls == []
    env.graph.users = [entra(userPrincipalName="BINH.NT@corp.vn")]  # case-insensitive match
    del env.graph.free[SKU_F1]
    out = await ms.m365_license(c)
    assert out.status == "failed" and "F1" in out.message and env.graph.calls == []


async def test_license_wait_timeouts_come_from_settings(env) -> None:  # type: ignore[no-untyped-def]
    env.settings = cfg(entra_wait_hours=1, mailbox_wait_hours=1)
    plan_out = {"account_plan": PLAN}
    assert (await ms.m365_license(ctx(outputs=plan_out, waited=1800))).status == "waiting"
    assert (await ms.m365_license(ctx(outputs=plan_out, waited=3700))).status == "failed"
    env.graph.users = [entra(assignedLicenses=[{"skuId": SKU_E3}, {"skuId": SKU_F1}])]
    assert (await ms.m365_license(ctx(outputs=plan_out, waited=3700))).status == "waiting"
    assert (await ms.m365_license(ctx(outputs=plan_out, waited=7300))).status == "failed"


async def test_plan_ou_and_custom_sam_rules(env) -> None:  # type: ignore[no-untyped-def]
    prep = (await ms.prepare_account_plan(ctx())).output
    good = prep["proposed_sam"]
    for ou in (f"OU=Ban_Users,{BASE}", f"OU=NotConfigured,{BASE}"):
        with pytest.raises(AppError):
            await ms.run_account_plan(ctx(prep, {"sam": good, "ou": ou}))
    env.perms = set()  # operator: proposed only
    assert (await ms.run_account_plan(ctx(prep, {"sam": good}))).status == "done"
    for inp in ({"sam": "an.custom"}, {"sam": good, "ou": f"OU=IT,{BASE}"}):
        with pytest.raises(AppError) as e:
            await ms.run_account_plan(ctx(prep, inp))
        assert e.value.code == "forbidden"
    env.perms = {ASSIGN}
    assert (await ms.run_account_plan(ctx(prep, {"sam": "an.custom"}))).output["custom"]


async def test_long_department_is_truncated_with_warning(env) -> None:  # type: ignore[no-untyped-def]
    long_dept = "P" * 80
    prep = (await ms.prepare_account_plan(ctx(emp={**EMP, "department": long_dept}))).output
    assert prep["department"] == "P" * 64 and any("64" in w for w in prep["warnings"])
    _, out = await plan(env)
    c = ctx(outputs={"account_plan": {**out.output, "department": long_dept}})
    await ms.ad_account(c)
    assert next(a for k, a in env.ad.calls if k == "add")["department"] == "P" * 64


def test_summary_html_is_escaped() -> None:
    from app.modules.lifecycle.workflows import _summary_html

    step = SimpleNamespace(key="rtr", seq=2, status="done", title="<b>T</b>", message="<script>x</script>")
    c = SimpleNamespace(
        params={"employee": {"id": "1", "full_name": "A <i>B</i>"}, "kind": "onboard"},
        step=SimpleNamespace(key="close"),
        job=SimpleNamespace(steps=[step]),
    )
    out = _summary_html(c)
    assert "<script>" not in out and "<i>" not in out and "&lt;script&gt;" in out and "&lt;b&gt;" in out


async def test_plan_offers_configured_ous_without_ban(env) -> None:  # type: ignore[no-untyped-def]
    """The OU choices travel with the plan (no settings permission needed) and never include Ban_Users."""
    out = (await ms.prepare_account_plan(ctx())).output
    ban = env.settings.ad_ban_ou
    assert ban and out["ou_options"]
    assert env.settings.ad_default_ou in out["ou_options"]
    assert not any(ms.inside(o, ban) for o in out["ou_options"])
