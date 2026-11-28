"""Transfer steps transfer_plan (manual) + ad_transfer (auto) with a fake AD."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import respx
from sqlalchemy import delete, select

from app.core.audit.models import AuditLog
from app.core.db import get_sessionmaker
from app.core.errors import AppError
from app.core.timeutil import today_vn
from app.core.workflow.definitions import StepOutcome
from app.core.workflow.models import Job
from app.modules.lifecycle import microsoft as ms
from app.modules.lifecycle import workflows as wf
from app.modules.lifecycle.models import LifecycleRequest
from app.modules.lifecycle.settings import AllocationMatrix, MatrixCell, MatrixDevice, MatrixLevel, OuRule
from app.modules.lifecycle.workflows import TRANSFER
from tests.test_ms_offboard import (
    DN,
    EMP,
    OffAd,
    acct,
    assert_audit_has_no_password,
    confirm,
    install,
    left,
    ms_on,  # noqa: F401  (fixture)
)
from tests.test_ms_onboard import BASE, GUID, FakeEx, FakeGraph, cfg
from tests.test_workflows import settle, step, systems  # noqa: F401  (fixture)

TODAY = today_vn().isoformat()
SALES, IT = f"OU=Sales,{BASE}", f"OU=IT,{BASE}"
G_SALES, G_IT, G_SHARED = (f"CN={n},OU=Groups,{BASE}" for n in ("G-Sales", "G-IT", "G-Shared"))
OTHER = f"CN=Unmanaged,OU=Groups,{BASE}"
MGR_DN = f"CN=Sếp Mới,{SALES}"


class TAd(OffAd):
    def __init__(self) -> None:
        super().__init__()
        self.groups: dict[str, dict] = {
            g: {"objectClass": ["top", "group"], "adminCount": 0} for g in (G_SALES, G_IT, G_SHARED)
        }

    async def get_group(self, dn):  # type: ignore[no-untyped-def]
        return self.groups.get(dn)

    async def add_member(self, group, user):  # type: ignore[no-untyped-def]
        self.calls.append(("add_member", group))
        next(u for u in self.users if u["dn"] == user)["memberOf"].append(group)

    async def remove_member(self, group, user):  # type: ignore[no-untyped-def]
        self.calls.append(("remove_member", group))
        next(u for u in self.users if u["dn"] == user)["memberOf"].remove(group)

    async def modify(self, dn, changes):  # type: ignore[no-untyped-def]
        self.calls.append(("modify", dict(changes)))
        next(u for u in self.users if u["dn"] == dn).update(changes)


@pytest.fixture
def env(monkeypatch):  # type: ignore[no-untyped-def]
    ad = TAd()
    ad.users = [
        acct(memberOf=[G_SALES, G_SHARED, OTHER], title="Sales", department="Sales"),
        acct(dn=MGR_DN, employeeID="000777", sAMAccountName="sep", displayName="Sếp Mới", memberOf=[]),
    ]
    settings = cfg(
        ad_ou_map=[
            OuRule(department="Sales", ou=SALES, groups=[G_SALES, G_SHARED]),
            OuRule(department="IT", ou=IT, groups=[G_IT, G_SHARED]),
        ]
    )
    e = SimpleNamespace(ad=ad, ex=FakeEx(), graph=FakeGraph(), settings=settings)
    install(monkeypatch, e)

    async def can(_ctx):  # type: ignore[no-untyped-def]
        return e.can

    e.can = True
    monkeypatch.setattr(ms, "_can_assign", can)
    return e


def ctx(
    emp: dict | None = None,
    *,
    output: dict | None = None,
    outputs: dict | None = None,
    inp: dict | None = None,
    **params: Any,
):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        session=None,
        params={"employee": {**EMP, **(emp or {})}, "microsoft": True, "kind": "transfer", **params},
        step=SimpleNamespace(output=output or {}),
        input=inp or {},
        outputs=outputs or {},
    )


async def plan_and_pin(emp: dict, inp: dict | None = None, **params: Any) -> dict:
    params.setdefault("effective_date", TODAY)
    out = (await ms.prepare_transfer_plan(ctx(emp, **params))).output
    pinned = await ms.run_transfer_plan(ctx(emp, output=out, inp=inp, **params))
    assert pinned.status == "done"
    return pinned.output


def run(emp: dict, pinned: dict, **params: Any):  # type: ignore[no-untyped-def]
    return ms.ad_transfer(ctx(emp, outputs={"transfer_plan": pinned}, **params))


def test_workflow_steps_follow_microsoft_flag() -> None:
    s = {x.key: x for x in TRANSFER.steps}
    assert [k for k in s] == ["check_request", "find_sdp_user", "transfer_plan", "ad_transfer", "m365_relicense", "biostar_transfer", "transfer_assets", "groups_remove", "groups_add", "rtr_checklist", "close_request"]  # fmt: skip
    on = [x.key for x in TRANSFER.steps if x.when({"microsoft": True})]
    assert on == ["check_request", "find_sdp_user", "transfer_plan", "ad_transfer", "m365_relicense", "biostar_transfer", "transfer_assets", "rtr_checklist", "close_request"]  # fmt: skip
    off = [x.key for x in TRANSFER.steps if x.when({})]  # Microsoft off: exactly the old manual flow
    assert off == ["check_request", "find_sdp_user", "groups_remove", "groups_add", "close_request"]
    assert s["m365_relicense"].writes == ("m365",) and s["biostar_transfer"].writes == ("biostar",)
    assert s["transfer_assets"].kind == "manual" and s["m365_relicense"].skippable
    for k in ("transfer_plan", "ad_transfer"):
        assert s[k].when({"microsoft": True}) and not s[k].when({}) and not s[k].skippable
    for k in ("groups_remove", "groups_add"):
        assert not s[k].when({"microsoft": True}) and s[k].when({}) and s[k].when({"microsoft": False})
    a = s["ad_transfer"]
    assert a.kind == "auto" and a.confirm and a.writes == ("ad",)
    assert a.checklist == ("Thuộc tính, OU và nhóm AD đúng theo kế hoạch",)


async def test_manager_only_changes_only_the_manager(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"manager_employee_id": "000777"}
    plan = (await ms.prepare_transfer_plan(ctx(emp))).output
    assert plan["attributes"] == {"manager_employee_id": "000777"} and plan["target_ou"] is None
    assert plan["add"] == [] and plan["remove"] == [] and plan["blocked"] is False
    out = await run(emp, await plan_and_pin(emp))  # the DN is resolved at run time
    assert out.status == "done" and env.ad.calls == [("modify", {"manager": MGR_DN})]
    assert env.ad.users[0]["memberOf"] == [G_SALES, G_SHARED, OTHER] and env.ad.users[0]["dn"] == DN


async def test_unknown_manager_is_a_warning_and_never_written(env) -> None:  # type: ignore[no-untyped-def]
    plan = (
        await ms.prepare_transfer_plan(ctx({"manager_employee_id": "000999"}, effective_date=TODAY))
    ).output
    assert plan["attributes"] == {} and "quản lý" in plan["warnings"][0]


async def test_full_transfer_order_shared_and_unmanaged_kept_then_idempotent(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"department_only": "IT", "department": "IT", "job_title": "Dev"}
    pinned = await plan_and_pin(emp)
    assert pinned["add"] == [G_IT] and pinned["remove"] == [G_SALES] and pinned["target_ou"] == IT
    out = await run(emp, pinned)
    assert out.status == "done" and [c[0] for c in env.ad.calls] == [
        "add_member",
        "modify",
        "move",
        "remove_member",
    ]
    assert env.ad.calls[1] == ("modify", {"title": "Dev", "department": "IT"})
    u = env.ad.users[0]
    assert u["dn"] == f"CN=Phạm Thị Bình,{IT}" and sorted(u["memberOf"]) == sorted([G_SHARED, OTHER, G_IT])
    assert out.before_state["memberOf"] == [G_SALES, G_SHARED, OTHER] and out.before_state["dn"] == DN
    assert out.output["added"] == 1 and out.output["removed"] == 1 and out.output["moved"]
    env.ad.calls.clear()
    again = await run(emp, pinned)
    assert (
        again.status == "done"
        and env.ad.calls == []
        and again.output["added"] == again.output["removed"] == 0
    )


async def test_unit_only_borrows_department_from_old_values(env) -> None:  # type: ignore[no-untyped-def]
    env.settings.ad_ou_map.append(OuRule(department="IT", unit="Infra", groups=[G_IT]))
    plan = (await ms.prepare_transfer_plan(ctx({"unit": "Infra"}, employee_old={"department": "IT"}))).output
    assert G_IT in plan["add"] and plan["sources"]["org"] == "request_old"


async def test_invalid_and_admin_groups_are_excluded_with_warning(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.groups[G_IT]["adminCount"] = 1
    env.ad.groups[G_SALES]["objectClass"] = ["top", "user"]
    plan = (await ms.prepare_transfer_plan(ctx({"department_only": "IT"}, effective_date=TODAY))).output
    assert plan["add"] == [] and plan["remove"] == []
    assert len(plan["warnings"]) == 2 and any("adminCount" in w for w in plan["warnings"])
    env.settings.ad_group_base = f"OU=Elsewhere,{BASE}"
    env.ad.groups[G_IT]["adminCount"] = 0
    assert G_IT not in (await ms.prepare_transfer_plan(ctx({"department_only": "IT"}))).output["add"]


async def test_waits_before_the_effective_date(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"department_only": "IT"}
    when = today_vn() + timedelta(days=3)
    pinned = await plan_and_pin(emp, effective_date=when.isoformat())
    out = await run(emp, pinned)
    assert out.status == "waiting" and out.retry_in_seconds == 3600
    assert out.message == f"Chờ ngày hiệu lực {when:%d/%m/%Y}" and env.ad.calls == []
    pinned["effective_date"] = today_vn().isoformat()
    assert (await run(emp, pinned)).status == "done"


async def test_cn_collision_renames_with_employee_id(env) -> None:  # type: ignore[no-untyped-def]
    env.ad.users.append(acct(dn=f"CN=Phạm Thị Bình,{IT}", employeeID="009999", sAMAccountName="other"))
    emp = {"department_only": "IT"}
    out = await run(emp, await plan_and_pin(emp))
    new = "CN=Phạm Thị Bình (002100)"
    assert out.status == "done" and ("move", DN, IT, new) in env.ad.calls
    assert env.ad.calls[-1] == ("remove_member", G_SALES)  # removal uses the re-read DN
    assert out.before_state["rdn"] == "CN=Phạm Thị Bình" and out.output["dn"] == f"{new},{IT}"


async def test_unchecking_a_removal_or_changing_ou_needs_assign(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"department_only": "IT"}
    plan = (await ms.prepare_transfer_plan(ctx(emp, effective_date=TODAY))).output
    env.can = False
    for inp in ({"remove": []}, {"ou": SALES}):
        with pytest.raises(AppError) as e:
            await ms.run_transfer_plan(ctx(emp, output=plan, inp=inp))
        assert e.value.code == "forbidden"
    ok = await ms.run_transfer_plan(ctx(emp, output=plan, inp={"remove": [G_SALES]}))
    assert ok.output["remove"] == [G_SALES]
    with pytest.raises(AppError) as e:
        await ms.run_transfer_plan(ctx(emp, output=plan, inp={"remove": [G_SHARED]}))
    assert e.value.code == "group_not_in_plan"
    env.can = True
    kept = await ms.run_transfer_plan(ctx(emp, output=plan, inp={"remove": []}))
    assert kept.output["remove"] == []
    out = await run(emp, kept.output)
    assert ("remove_member", G_SALES) not in env.ad.calls and out.output["removed"] == 0


async def test_ou_choice_must_be_configured_and_guard_failures_block(env) -> None:  # type: ignore[no-untyped-def]
    plan = (await ms.prepare_transfer_plan(ctx({"department_only": "IT"}, effective_date=TODAY))).output
    with pytest.raises(AppError) as e:
        await ms.run_transfer_plan(ctx(output=plan, inp={"ou": f"OU=Nowhere,{BASE}"}))
    assert e.value.code == "ou_not_configured"
    env.ad.users[0]["adminCount"] = 1
    blocked = await ms.prepare_transfer_plan(ctx({"department_only": "IT"}))
    assert blocked.output["blocked"]
    with pytest.raises(AppError):
        await ms.run_transfer_plan(ctx(output=blocked.output))
    assert (
        await run({}, {"pinned": True, "employee_id": "002100", "add": [], "remove": []})
    ).status == "failed"


async def test_skipped_when_microsoft_disabled(env) -> None:  # type: ignore[no-untyped-def]
    env.settings = cfg(microsoft_enabled=False)
    assert (await ms.prepare_transfer_plan(ctx())).status == "skipped"
    assert (await run({}, {"pinned": True})).status == "skipped" and env.ad.calls == []


async def test_preview_lists_exact_changes(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"department_only": "IT"}
    text = "\n".join(
        await ms.preview_ad_transfer(ctx(emp, outputs={"transfer_plan": await plan_and_pin(emp)}))
    )
    assert "Thêm 1 nhóm, gỡ 1 nhóm, chuyển OU" in text and IT in text


# ------------------------------------------------------------------ m365_relicense / biostar_transfer / assets / RTR
SKU_E3, SKU_F1 = "aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000002"
UPN = "binh.pt@corp.vn"
STAFF = MatrixLevel(name="Staff", titles=["Staff"], license_skus=[SKU_F1], license_names=["F1"])
MANAGER = MatrixLevel(name="Manager", titles=["Manager"], license_skus=[SKU_E3], license_names=["E3"])
MATRIX = AllocationMatrix(levels=[STAFF, MANAGER], cells=[[], []])
DEVICES = [
    MatrixDevice(label="Laptop", product_types=["Laptop"], catalog_name="Dell 5440"),
    MatrixDevice(label="Màn hình", product_types=["Monitor"]),
]
ASSET_MATRIX = AllocationMatrix(
    devices=DEVICES,
    levels=[STAFF, MANAGER],
    cells=[[MatrixCell(), MatrixCell()], [MatrixCell(on=True), MatrixCell(on=True, quantity=2)]],
)


class RGraph:
    def __init__(self, licenses: list[str], by_group: tuple[str, ...] = ()) -> None:
        self.user = {
            "id": GUID,
            "employeeId": "002100",
            "userPrincipalName": UPN,
            "assignedLicenses": [{"skuId": k} for k in licenses],
            "licenseAssignmentStates": [
                {"skuId": k, "assignedByGroup": "grp-1" if k in by_group else None} for k in licenses
            ],
        }
        self.free = {SKU_E3: 3, SKU_F1: 3}
        self.calls: list[tuple] = []

    async def find_user_by_employee_id(self, emp):  # type: ignore[no-untyped-def]
        return [self.user] if emp == "002100" else []

    async def find_user_by_upn(self, upn):  # type: ignore[no-untyped-def]
        return self.user if upn == UPN else None

    async def subscribed_skus(self):  # type: ignore[no-untyped-def]
        return [{"skuId": k, "enabled": v, "consumed": 0} for k, v in self.free.items()]

    async def swap_licenses(self, uid, add, remove):  # type: ignore[no-untyped-def]
        self.calls.append(("swap", list(add), list(remove)))
        keep = [a for a in self.user["assignedLicenses"] if a["skuId"] not in remove]
        self.user["assignedLicenses"] = keep + [{"skuId": k} for k in add]
        states = [x for x in self.user["licenseAssignmentStates"] if x["skuId"] not in remove]
        self.user["licenseAssignmentStates"] = states + [{"skuId": k, "assignedByGroup": None} for k in add]


def pinned_plan(**over: Any) -> dict:
    return {
        "pinned": True,
        "employee_id": "002100",
        "upn": UPN,
        "effective_date": TODAY,
        "licenses": {
            "add": [SKU_E3],
            "remove": [SKU_F1],
            "level": "Manager",
            "names": {SKU_E3: "E3", SKU_F1: "F1"},
        },
        **over,
    }


def relic_ctx(**over: Any):  # type: ignore[no-untyped-def]
    return ctx(outputs={"transfer_plan": pinned_plan(**over), "ad_transfer": {"upn": UPN}})


@pytest.fixture
def relic(env):  # type: ignore[no-untyped-def]
    env.settings = cfg(allocation_matrix=MATRIX)
    env.graph = RGraph([SKU_F1])
    return env


async def test_relicense_swaps_in_one_call_and_is_idempotent(relic) -> None:  # type: ignore[no-untyped-def]
    out = await ms.m365_relicense(relic_ctx())
    assert out.status == "done" and relic.graph.calls == [("swap", [SKU_E3], [SKU_F1])]
    assert out.before_state == {"assigned": [SKU_F1]} and out.output["removed"] == [SKU_F1]
    relic.graph.calls.clear()
    again = await ms.m365_relicense(relic_ctx())
    assert again.status == "done" and relic.graph.calls == [] and again.output["added"] == []


async def test_relicense_skipped_waits_and_refuses(relic) -> None:  # type: ignore[no-untyped-def]
    assert (await ms.m365_relicense(relic_ctx(licenses={"add": [], "remove": []}))).status == "skipped"
    with pytest.raises(AppError):  # an unpinned plan never runs
        await ms.m365_relicense(ctx(outputs={"transfer_plan": {"licenses": {"add": [SKU_E3]}}}))
    relic.settings = cfg(allocation_matrix=MATRIX, microsoft_enabled=False)
    assert (await ms.m365_relicense(relic_ctx())).status == "skipped"
    relic.settings = cfg(allocation_matrix=MATRIX)
    soon = (today_vn() + timedelta(days=2)).isoformat()
    w = await ms.m365_relicense(relic_ctx(effective_date=soon))
    assert w.status == "waiting" and w.retry_in_seconds == 3600 and relic.graph.calls == []
    assert (await ms.m365_relicense(relic_ctx(effective_date=None))).status == "failed"
    relic.graph.free[SKU_E3] = 0
    w = await ms.m365_relicense(relic_ctx())
    assert w.status == "waiting" and "E3" in w.message and w.retry_in_seconds == 1800
    assert relic.graph.calls == []
    del relic.graph.free[SKU_E3]
    assert "không có trong tenant" in (await ms.m365_relicense(relic_ctx())).message
    relic.graph.free[SKU_E3] = 1
    relic.graph.user["userPrincipalName"] = "other@corp.vn"
    bad = await ms.m365_relicense(relic_ctx())
    assert bad.status == "failed" and "UPN" in bad.message and relic.graph.calls == []


async def test_relicense_never_removes_group_inherited_or_unmanaged_or_unpinned_skus(relic) -> None:  # type: ignore[no-untyped-def]
    other = "aaaaaaaa-0000-0000-0000-0000000000ff"
    relic.graph = RGraph([SKU_F1, other], by_group=(SKU_F1,))  # F1 comes from a group
    out = await ms.m365_relicense(
        relic_ctx(licenses={**pinned_plan()["licenses"], "remove": [SKU_F1, other]})
    )
    assert out.status == "done" and relic.graph.calls == [
        ("swap", [SKU_E3], [])
    ]  # inherited + unmanaged kept
    relic.graph = RGraph([SKU_F1])
    relic.graph.user.pop("licenseAssignmentStates")  # no state info: nothing counts as direct
    await ms.m365_relicense(relic_ctx())
    assert relic.graph.calls == [("swap", [SKU_E3], [])]
    relic.settings = cfg(
        allocation_matrix=AllocationMatrix(levels=[MANAGER], cells=[[]])
    )  # F1 left the matrix
    relic.graph = RGraph([SKU_F1])
    await ms.m365_relicense(relic_ctx())
    assert relic.graph.calls == [("swap", [SKU_E3], [])]


async def test_relicense_finds_entra_by_upn_when_employee_id_missing(relic) -> None:  # type: ignore[no-untyped-def]
    relic.graph.user["employeeId"] = None
    out = await ms.m365_relicense(relic_ctx())
    assert out.status == "done" and relic.graph.calls == [("swap", [SKU_E3], [SKU_F1])]
    relic.graph.user = {**relic.graph.user, "userPrincipalName": "x@corp.vn"}
    relic.graph.find_user_by_upn = lambda upn: _none()  # type: ignore[method-assign,assignment]
    assert (await ms.m365_relicense(relic_ctx())).status == "failed"


async def test_plan_license_lines_only_direct_matrix_skus(relic) -> None:  # type: ignore[no-untyped-def]
    emp = {"job_title": "Manager", "department_only": "IT"}
    lic = (await ms.prepare_transfer_plan(ctx(emp, effective_date=TODAY))).output["licenses"]
    assert lic["add"] == [SKU_E3] and lic["remove"] == [SKU_F1] and lic["names"][SKU_F1] == "F1"
    relic.graph = RGraph([SKU_F1], by_group=(SKU_F1,))
    lic = (await ms.prepare_transfer_plan(ctx(emp, effective_date=TODAY))).output["licenses"]
    assert lic["remove"] == [] and lic["add"] == [SKU_E3]
    assert (await ms.prepare_transfer_plan(ctx({"department_only": "IT"}))).output["licenses"]["add"] == []


async def _none() -> None:
    return None


class FakeBs:
    def __init__(self, user: dict | None) -> None:
        self.user, self.puts = user, []

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *exc):  # type: ignore[no-untyped-def]
        return None

    async def get_user(self, uid):  # type: ignore[no-untyped-def]
        return self.user

    async def update_user(self, uid, body):  # type: ignore[no-untyped-def]
        self.puts.append((uid, body))


@pytest.fixture
def bs(env, monkeypatch):  # type: ignore[no-untyped-def]
    env.settings = cfg(
        biostar_access_group_ids=["7"],
        ad_ou_map=[
            OuRule(department="Sales", ou=SALES, biostar_access_group_ids=["10"]),
            OuRule(department="IT", ou=IT, biostar_access_group_ids=["20"]),
        ],
    )
    env.bs = FakeBs(
        {"user_id": "002100", "disabled": "false", "access_groups": [{"id": "7"}, {"id": "10"}, {"id": "99"}]}
    )

    async def open_bs(_ctx, _purpose):  # type: ignore[no-untyped-def]
        return env.bs

    monkeypatch.setattr(wf, "_biostar", open_bs)

    async def load(_s):  # type: ignore[no-untyped-def]
        return env.settings

    monkeypatch.setattr(wf, "load_module_settings", load)
    return env


def bs_ctx(add: list[str], remove: list[str], **over: Any):  # type: ignore[no-untyped-def]
    plan = pinned_plan(biostar={"add": add, "remove": remove}, **over)
    return ctx(outputs={"transfer_plan": plan})


async def test_biostar_transfer_puts_pinned_lines_once(bs) -> None:  # type: ignore[no-untyped-def]
    c = bs_ctx(["20"], ["10"])
    out = await wf.biostar_transfer(c)
    assert out.status == "done" and bs.bs.puts == [
        ("002100", {"access_groups": [{"id": "7"}, {"id": "99"}, {"id": "20"}]})
    ]
    assert out.before_state == {"access_group_ids": ["7", "10", "99"]}
    assert out.output["added"] == ["20"] and out.output["removed"] == ["10"]
    bs.bs.user["access_groups"] = [{"id": "7"}, {"id": "99"}, {"id": "20"}]
    bs.bs.puts.clear()
    assert (await wf.biostar_transfer(c)).output["changed"] is False and bs.bs.puts == []


async def test_biostar_transfer_removes_only_the_intersection_with_pinned_and_managed(bs) -> None:  # type: ignore[no-untyped-def]
    bs.bs.user["access_groups"] = [{"id": "7"}, {"id": "10"}, {"id": "99"}, {"id": "55"}]
    # 99 / 55 are unmanaged, "10" is pinned and managed, "30" is pinned but not on the person
    await wf.biostar_transfer(bs_ctx(["20"], ["10", "99", "30"]))
    assert [g["id"] for g in bs.bs.puts[0][1]["access_groups"]] == ["7", "99", "55", "20"]


async def test_biostar_transfer_without_access_groups_fails_without_put(bs) -> None:  # type: ignore[no-untyped-def]
    bs.bs.user = {"user_id": "002100", "disabled": "false"}
    out = await wf.biostar_transfer(bs_ctx(["20"], ["10"]))
    assert out.status == "failed" and bs.bs.puts == []


async def test_biostar_transfer_skips_and_waits(bs) -> None:  # type: ignore[no-untyped-def]
    c = bs_ctx(["20"], ["10"])
    bs.bs.user["disabled"] = "true"
    assert (await wf.biostar_transfer(c)).status == "skipped"
    bs.bs.user = None
    assert (await wf.biostar_transfer(c)).status == "skipped"
    assert (await wf.biostar_transfer(bs_ctx([], []))).status == "skipped"  # nothing pinned
    soon = (today_vn() + timedelta(days=1)).isoformat()
    bs.bs.user = {"user_id": "002100", "disabled": "false", "access_groups": []}
    assert (await wf.biostar_transfer(bs_ctx(["20"], [], effective_date=soon))).status == "waiting"
    assert bs.bs.puts == []
    with pytest.raises(AppError):  # not pinned
        await wf.biostar_transfer(ctx(outputs={"transfer_plan": {"biostar": {"add": ["20"]}}}))


async def test_plan_biostar_lines_come_from_the_read_state(bs, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def open_bs(_ctx, _purpose):  # type: ignore[no-untyped-def]
        return bs.bs

    monkeypatch.setattr(ms, "biostar_client", open_bs)
    out = (await ms.prepare_transfer_plan(ctx({"department_only": "IT"}, effective_date=TODAY))).output
    assert out["biostar"] == {"add": ["20"], "remove": ["10"]}
    bs.bs.user = {"user_id": "002100", "disabled": "false"}
    out = (await ms.prepare_transfer_plan(ctx({"department_only": "IT"}, effective_date=TODAY))).output
    assert out["biostar"] == {"add": [], "remove": []} and any("BioStar" in w for w in out["warnings"])


async def test_onboard_biostar_groups_are_global_plus_matching_rules(bs) -> None:  # type: ignore[no-untyped-def]
    bs.bs.user = None
    bs.bs.create_user = lambda payload: _created(bs.bs, payload)  # type: ignore[attr-defined]
    out = await wf.biostar_create(ctx({"department_only": "IT"}))
    assert out.status == "done"
    assert [g["id"] for g in bs.bs.created[0]["access_groups"]] == ["7", "20"]
    bs.settings = cfg(biostar_access_group_ids=["7"])  # unchanged when no rule declares ids
    await wf.biostar_create(ctx({"department_only": "IT"}))
    assert [g["id"] for g in bs.bs.created[1]["access_groups"]] == ["7"]


async def _created(b: FakeBs, payload: dict) -> None:
    b.__dict__.setdefault("created", []).append(payload)


async def test_transfer_assets_needs_excess_and_acknowledgement(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env.settings = cfg(allocation_matrix=ASSET_MATRIX)

    async def load(_s):  # type: ignore[no-untyped-def]
        return env.settings

    monkeypatch.setattr(wf, "load_module_settings", load)
    have = [
        {"id": "1", "name": "LT-1", "tag": "T1", "product_type": "Laptop"},
        {"id": "2", "name": "LT-2", "tag": "T2", "product_type": "Laptop"},
        {"id": "3", "name": "MN-3", "tag": "T3", "product_type": "Monitor"},
        {"id": "4", "name": "Chuột", "tag": "T4", "product_type": "Mouse"},
    ]

    async def collect(_ctx):  # type: ignore[no-untyped-def]
        return StepOutcome("waiting", "x", {"assets": have, "source": "SDP"})

    monkeypatch.setattr(wf, "prepare_collect", collect)
    c = ctx({"job_title": "Manager"})
    prep = await wf.prepare_transfer_assets(c)
    out = prep.output
    assert out["level"] == "Manager"
    assert [(n["label"], n["quantity"]) for n in out["needs"]] == [("Màn hình", 1)]
    assert [a["id"] for a in out["excess"]] == ["2"]  # the mouse is outside the standard: not flagged
    with pytest.raises(AppError) as e:
        await wf.run_transfer_assets(ctx(output=out))
    assert e.value.code == "assets_unconfirmed"
    ok = await wf.run_transfer_assets(ctx(output=out, inp={"acknowledged": True}))
    assert ok.status == "done"
    nothing = await wf.run_transfer_assets(ctx(output={"needs": [], "excess": []}))
    assert nothing.status == "done"
    unknown = await wf.prepare_transfer_assets(ctx({"job_title": "Zzz"}))
    assert unknown.output["needs"] == [] and "cấp bậc" in unknown.message


async def test_transfer_rtr_summary_lists_every_change() -> None:
    outputs = {
        "transfer_plan": {
            "ou": SALES,
            "target_ou": IT,
            "add": [G_IT],
            "remove": [G_SALES],
            "attributes": {"title": "Dev", "manager": MGR_DN},
            "current": {"title": "Sales"},
        },
        "ad_transfer": {"added": 1, "removed": 1},
        "m365_relicense": {"added": [SKU_E3], "removed": [SKU_F1]},
        "biostar_transfer": {"changed": True, "added": ["20"], "removed": ["10"]},
        "transfer_assets": {
            "needs": [{"label": "Màn hình", "quantity": 1}],
            "excess": [{"name": "LT-2", "tag": "T2"}],
        },
    }
    c = ctx(outputs=outputs)
    text = (await ms.prepare_rtr_checklist(c)).output["summary"]
    assert text == (await ms.run_rtr_checklist(c)).output["summary"]
    assert text.startswith("Thuyên chuyển – Phạm Thị Bình (Mã NV 002100)")
    for part in (
        f"{SALES} → {IT}",
        "Nhóm AD: thêm 1, gỡ 1",
        "Chức danh: Sales → Dev",
        "Quản lý trực tiếp",
        "License: thêm 1, gỡ 1",
        "BioStar: thêm 1 nhóm quyền, gỡ 1",
        "Màn hình x1",
        "LT-2 (T2)",
    ):
        assert part in text


# ------------------------------------------------------------------ Microsoft-enabled end to end
def _transfer_request(rid: str, **fields: Any) -> LifecycleRequest:
    return LifecycleRequest(
        id=rid,
        display_id=f"79{rid[-2:]}",
        subject="RTR thuyên chuyển",
        employee_id="002100",
        full_name="Phạm Thị Bình",
        change_type_raw="Thuyên chuyển",
        kind="transfer",
        effective_date=today_vn() - timedelta(days=1),
        fields=fields,
        parse_errors=[],
        description_text="",
    )


@pytest.fixture
async def transfer_env(admin, systems, ms_on):  # type: ignore[no-untyped-def]  # noqa: F811
    """Microsoft on, department rules with AD groups + BioStar groups, a Manager matrix row, a transfer request."""
    e = ms_on
    rules = [
        OuRule(department="Sales", ou=SALES, groups=[G_SALES, G_SHARED], biostar_access_group_ids=["10"]),
        OuRule(department="IT", ou=IT, groups=[G_IT, G_SHARED], biostar_access_group_ids=["20"]),
    ]
    e.ad = TAd()
    e.ad.users = [acct(memberOf=[G_SALES, G_SHARED, OTHER], title="Sales", department="Sales")]
    e.graph = RGraph([SKU_F1])
    e.settings = cfg(
        ad_ou_map=rules,
        allocation_matrix=MATRIX,
        ad_ban_ou=f"OU=Ban_Users,{BASE}",
        biostar_access_group_ids=["7"],
        biostar_profile_id=e.settings.biostar_profile_id,  # same BioStar as the saved settings
    )
    values = (await admin.get("/api/lifecycle/settings")).json()["values"]
    extra = {
        "ad_ou_map": [r.model_dump(mode="json") for r in rules],
        "allocation_matrix": MATRIX.model_dump(mode="json"),
        "biostar_access_group_ids": ["7"],
    }
    r = await admin.put("/api/lifecycle/settings", json={**values, **extra})
    assert r.status_code == 200, r.text
    systems.requests["9103"] = "Open"
    async with get_sessionmaker()() as s:
        s.add(_transfer_request("9103", department="IT", job_title="Manager"))
        await s.commit()
    yield e
    async with get_sessionmaker()() as s:
        await s.execute(delete(Job).where(Job.external_ref == "9103"))
        await s.execute(delete(LifecycleRequest).where(LifecycleRequest.id == "9103"))
        await s.commit()
    # ms_on restores the saved settings afterwards


@respx.mock(assert_all_called=False)
async def test_transfer_end_to_end_with_microsoft(admin, systems, transfer_env) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    e = transfer_env
    j = (await admin.post("/api/lifecycle/jobs", json={"request_id": "9103"})).json()
    assert [x["key"] for x in j["steps"]] == [x.key for x in TRANSFER.steps if x.when({"microsoft": True})]
    j = (await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})).json()
    j = await settle(admin, j["id"])
    plan = step(j, "transfer_plan")
    assert plan["status"] == "waiting" and plan["output"]["add"] == [G_IT] and e.ad.calls == []
    r = await admin.post(f"/api/core/jobs/{j['id']}/steps/transfer_plan/complete", json={"data": {}})
    assert r.status_code == 200, r.text
    j = await settle(admin, j["id"])
    assert (
        step(j, "ad_transfer")["status"] == "waiting" and e.ad.calls == [] and e.graph.calls == []
    )  # confirm first
    j = await confirm(admin, j, "ad_transfer", "002100")
    assert step(j, "ad_transfer")["status"] == "done" and e.ad.users[0]["dn"] == f"CN=Phạm Thị Bình,{IT}"
    j = await settle(admin, j["id"])
    assert step(j, "m365_relicense")["status"] == "waiting" and e.graph.calls == []  # confirm first
    j = await confirm(admin, j, "m365_relicense", "002100")
    assert step(j, "m365_relicense")["status"] == "done" and e.graph.calls == [("swap", [SKU_E3], [SKU_F1])]
    assert step(j, "m365_relicense")["before_state"] == {"assigned": [SKU_F1]}
    assert step(j, "biostar_transfer")["status"] == "waiting"
    j = await confirm(admin, j, "biostar_transfer", "002100")
    assert step(j, "biostar_transfer")["status"] == "done"
    groups = [g["id"] for g in systems.bs_users["002100"]["access_groups"]]
    assert groups == ["1", "7", "20"]  # unmanaged kept, global list, then the new department
    assert step(j, "transfer_assets")["status"] == "waiting"
    r = await admin.post(
        f"/api/core/jobs/{j['id']}/steps/transfer_assets/complete", json={"data": {"acknowledged": True}}
    )
    assert r.status_code == 200, r.text
    j = await settle(admin, j["id"])
    rtr = step(j, "rtr_checklist")
    assert rtr["status"] == "waiting" and "Thuyên chuyển" in rtr["output"]["summary"]
    assert "License: thêm 1, gỡ 1" in rtr["output"]["summary"] and "BioStar" in rtr["output"]["summary"]
    r = await admin.post(f"/api/core/jobs/{j['id']}/steps/rtr_checklist/complete", json={"data": {}})
    assert r.status_code == 200, r.text
    j = await settle(admin, j["id"])
    assert j["status"] == "completed", left(j)
    assert systems.requests["9103"] == "Resolved"
    await assert_audit_has_no_password([j["id"]])


async def test_sync_auto_create_makes_a_microsoft_transfer_job(admin, transfer_env) -> None:  # type: ignore[no-untyped-def]
    from app.modules.lifecycle.jobs_api import auto_create_jobs

    async with get_sessionmaker()() as s:
        before = set((await s.scalars(select(Job.id))).all())
        try:
            assert await auto_create_jobs(s) >= 1
            await s.commit()
            job = await s.scalar(select(Job).where(Job.external_ref == "9103"))
            assert job is not None and job.params["microsoft"] is True and job.params["kind"] == "transfer"
            assert [x.key for x in job.steps] == [
                x.key for x in TRANSFER.steps if x.when({"microsoft": True})
            ]
        finally:  # jobs made for other leftover requests must not leak into other tests
            await s.execute(delete(Job).where(Job.id.not_in(before)))
            await s.commit()


# ------------------------------------------------------------------ Gate 2 (C-11..C-18)
HCM = "HCM"
G_INFRA = f"CN=G-Infra,OU=Groups,{BASE}"


@pytest.fixture
def divs(env):  # type: ignore[no-untyped-def]
    """Rules with a division; a default OU exists and must never be proposed."""
    env.ad.groups[G_INFRA] = {"objectClass": ["top", "group"], "adminCount": 0}
    env.settings = cfg(
        ad_default_ou=f"OU=Default,{BASE}",
        ad_ou_map=[
            OuRule(division=HCM, department="Sales", ou=SALES, groups=[G_SALES, G_SHARED]),
            OuRule(division=HCM, department="IT", ou=IT, groups=[G_IT, G_SHARED]),
            OuRule(division=HCM, department="IT", unit="Infra", groups=[G_INFRA]),
        ],
    )
    return env


async def plan_of(emp: dict, **params: Any) -> dict:
    params.setdefault("effective_date", TODAY)
    return (await ms.prepare_transfer_plan(ctx(emp, **params))).output


async def test_c11_only_department_never_removes_what_it_cannot_judge(divs) -> None:  # type: ignore[no-untyped-def]
    plan = await plan_of({"department_only": "IT", "department": "IT"})  # division unknown
    assert plan["remove"] == [G_SALES] and plan["add"] == []  # shared group belongs to an undecidable rule
    assert plan["target_ou"] is None and any("Chưa đủ thông tin" in w for w in plan["warnings"])
    full = await plan_of(
        {"department_only": "IT", "department": "IT"},
        employee_old={"division": HCM, "department_only": "Sales"},
    )
    assert full["add"] == [G_IT] and full["remove"] == [G_SALES] and full["target_ou"] == IT
    assert full["sources"]["org"] == "request_old" and full["org"] == {
        "division": HCM,
        "department": "IT",
        "unit": "",
    }


async def test_c11_title_change_with_division_keeps_everything(divs) -> None:  # type: ignore[no-untyped-def]
    plan = await plan_of({"job_title": "Dev", "division": HCM})
    assert plan["add"] == [] and plan["remove"] == [] and plan["target_ou"] is None
    assert plan["attributes"] == {"title": "Dev"} and plan["org"]["department"] == "Sales"


async def test_c11_only_unit_removes_nothing_and_proposes_no_default_ou(divs) -> None:  # type: ignore[no-untyped-def]
    plan = await plan_of({"unit": "Infra"})
    assert plan["remove"] == [] and plan["add"] == [] and plan["target_ou"] is None
    assert plan["attributes"] == {"department": "Sales – Infra"}  # built from the full org (C-17)
    assert any("Chưa đủ thông tin" in w for w in plan["warnings"])


async def test_c12_duplicate_remove_payload_cannot_bypass_assign(env) -> None:  # type: ignore[no-untyped-def]
    env.settings = cfg(
        ad_ou_map=[
            OuRule(department="Sales", ou=SALES, groups=[G_SALES, G_SHARED]),
            OuRule(department="IT", ou=IT, groups=[G_IT]),
        ]
    )
    emp = {"department_only": "IT"}
    plan = await plan_of(emp)
    assert plan["remove"] == [G_SALES, G_SHARED]
    env.can = False
    with pytest.raises(AppError) as e:
        await ms.run_transfer_plan(ctx(emp, output=plan, inp={"remove": [G_SALES, G_SALES]}))
    assert e.value.code == "forbidden"
    env.can = True
    ok = await ms.run_transfer_plan(ctx(emp, output=plan, inp={"remove": [G_SALES, G_SALES]}))
    assert ok.output["remove"] == [G_SALES]


async def test_c13_unmanaged_group_is_never_removed_even_if_pinned(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"department_only": "IT"}
    pinned = await plan_and_pin(emp)
    pinned["remove"] = [G_SALES, OTHER]  # tampered / stale: OTHER is not managed by any rule
    out = await run(emp, pinned)
    assert (
        out.status == "done" and ("remove_member", OTHER) not in env.ad.calls
    )  # C-18: current settings rule
    assert ("remove_member", G_SALES) in env.ad.calls


async def test_c14_manager_is_resolved_at_run_and_a_missing_one_only_warns(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"manager_employee_id": "000777"}
    pinned = await plan_and_pin(emp)
    assert pinned["attributes"] == {"manager_employee_id": "000777"}
    del env.ad.users[1]  # the manager vanished before the run
    out = await run(emp, pinned)
    assert out.status == "done" and env.ad.calls == [] and "000777" in out.output["warnings"][0]


async def test_c15_missing_or_broken_effective_date_must_be_entered(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"department_only": "IT"}
    for bad in (None, "not-a-date"):
        plan = await plan_of(emp, effective_date=bad)
        assert plan["effective_date"] is None and any("ngày hiệu lực" in w for w in plan["warnings"])
        with pytest.raises(AppError) as e:
            await ms.run_transfer_plan(ctx(emp, output=plan))
        assert e.value.code == "effective_date_required"
        with pytest.raises(AppError):
            await ms.run_transfer_plan(ctx(emp, output=plan, inp={"effective_date": "31/12/2026"}))
        ok = await ms.run_transfer_plan(ctx(emp, output=plan, inp={"effective_date": TODAY}))
        assert ok.output["effective_date"] == TODAY and ok.output["pinned"]
    assert (await run(emp, {**ok.output, "effective_date": None})).status == "failed"
    soon = (today_vn() + timedelta(days=1)).isoformat()
    pinned = (await ms.run_transfer_plan(ctx(emp, output=await plan_of(emp, effective_date=None), inp={"effective_date": soon}))).output  # fmt: skip
    assert (await run(emp, pinned)).status == "waiting"


async def test_c16_group_that_stays_after_removal_fails(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"department_only": "IT"}
    pinned = await plan_and_pin(emp)

    async def lenient(group, user):  # type: ignore[no-untyped-def]  # like error 53: reports success, removes nothing
        env.ad.calls.append(("remove_member", group))

    env.ad.remove_member = lenient  # type: ignore[method-assign]
    out = await run(emp, pinned)
    assert out.status == "failed" and G_SALES in out.message and out.before_state["memberOf"]
    env.ad.users[0]["adminCount"] = None
    env.ad.find_users = lambda eid: _empty()  # type: ignore[method-assign,assignment]  # cannot read back
    assert (await run(emp, pinned)).status == "failed"


async def _empty() -> list:
    return []


async def test_c17_output_compares_first_seen_state_so_a_retry_still_reports_the_work(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"department_only": "IT", "unit": "Infra"}
    pinned = await plan_and_pin(emp)
    assert pinned["attributes"] == {"department": "IT – Infra"}
    first = await run(emp, pinned)
    assert first.output["added"] == 1 and first.output["removed"] == 1 and first.output["moved"]
    retry_ctx = ctx(emp, outputs={"transfer_plan": pinned})
    retry_ctx.step = SimpleNamespace(
        output={}, before_state=first.before_state
    )  # engine keeps the first state
    again = await ms.ad_transfer(retry_ctx)
    assert again.output["added"] == 1 and again.output["removed"] == 1 and again.output["moved"]
    assert again.output["attributes"] == ["department"]


@respx.mock(assert_all_called=False)
async def test_c14_reprepare_endpoint_scope_audit_and_unblocking(admin, client, transfer_env) -> None:  # type: ignore[no-untyped-def]
    e = transfer_env
    e.ad.users[0]["adminCount"] = 1  # the guard refuses: the plan is blocked
    j = (await admin.post("/api/lifecycle/jobs", json={"request_id": "9103"})).json()
    j = (await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})).json()
    j = await settle(admin, j["id"])
    assert step(j, "transfer_plan")["output"]["blocked"] is True
    roles = {r["code"]: r["id"] for r in (await admin.get("/api/core/roles")).json()}
    upn = "op-reprep@test.local"
    await admin.post(
        "/api/core/users", json={"upn": upn, "display_name": upn, "role_ids": [roles["operator"]]}
    )
    me = (await admin.get("/api/core/me")).json()
    async with get_sessionmaker()() as s:
        jb = await s.get(Job, uuid.UUID(j["id"]))
        jb.assignee_user_id, jb.assignee_name = uuid.UUID(me["id"]), "Admin"  # type: ignore[union-attr]
        await s.commit()
    url = f"/api/core/jobs/{j['id']}/steps/transfer_plan/prepare"
    await client.login(upn)
    assert (await client.post(url)).status_code == 403  # somebody else's job
    assert (await admin.post(f"/api/core/jobs/{j['id']}/steps/check_request/prepare")).status_code == 409
    e.ad.users[0]["adminCount"] = None
    assert (await admin.post(url)).status_code == 200
    j = await settle(admin, j["id"])
    assert (
        step(j, "transfer_plan")["status"] == "waiting"
        and step(j, "transfer_plan")["output"]["blocked"] is False
    )
    assert step(j, "transfer_plan")["output"]["add"] == [G_IT]
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(AuditLog.id).where(AuditLog.action == "workflow.step_reprepared", AuditLog.target_id == j["id"]))  # fmt: skip


async def test_c20_borrowed_department_no_rule_names_is_not_known(divs) -> None:  # type: ignore[no-untyped-def]
    for ad_dept in ("Kỹ thuật - Tổ 1", "P. Kỹ thuật"):
        divs.ad.users[0]["department"] = ad_dept
        plan = await plan_of({"unit": "Infra"})
        assert plan["remove"] == [] and plan["add"] == [] and plan["target_ou"] is None, ad_dept
        assert any("Chưa đủ thông tin" in w and "department" in w for w in plan["warnings"]), ad_dept


async def test_c21_title_on_the_default_row_never_removes_a_license(env) -> None:  # type: ignore[no-untyped-def]
    default = MatrixLevel(name="Mặc định", license_skus=[SKU_F1], license_names=["F1"])
    env.settings = cfg(allocation_matrix=AllocationMatrix(levels=[default, MANAGER], cells=[[], []]))
    env.graph = RGraph([SKU_E3])  # holds the higher license directly
    out = (
        await ms.prepare_transfer_plan(ctx({"job_title": "GĐ Khối Vận hành"}, effective_date=TODAY))
    ).output
    lic = out["licenses"]
    assert lic["add"] == [SKU_F1] and lic["remove"] == [] and lic["level_why"] == "hàng mặc định"
    assert any("không khớp cấp bậc" in w for w in out["warnings"])
    ok = (await ms.prepare_transfer_plan(ctx({"job_title": "Manager"}, effective_date=TODAY))).output
    assert ok["licenses"]["remove"] == [] and "không khớp cấp bậc" not in " ".join(
        ok["warnings"]
    )  # holds E3 already


async def test_c22_prepare_after_a_rejected_verify_is_not_pinned_and_messages_point_to_recreate(env) -> None:  # type: ignore[no-untyped-def]
    emp = {"department_only": "IT"}
    pinned = await plan_and_pin(emp)
    assert pinned["pinned"] is True
    again = (await ms.prepare_transfer_plan(ctx(emp, effective_date=TODAY))).output
    assert {**pinned, **again}["pinned"] is False  # the engine merges step output over the old one
    env.ad.groups[G_IT]["adminCount"] = 1  # the pinned group became privileged: the plan is stale
    out = await run(emp, pinned)
    assert out.status == "failed" and "huỷ Job và tạo lại" in out.message
    assert "huỷ Job và tạo lại" in (await run(emp, {**pinned, "effective_date": None})).message


@respx.mock(assert_all_called=False)
async def test_c22_a_cancelled_transfer_job_with_a_pinned_plan_can_be_recreated(admin, transfer_env) -> None:  # type: ignore[no-untyped-def]
    j = (await admin.post("/api/lifecycle/jobs", json={"request_id": "9103"})).json()
    j = (await admin.post(f"/api/core/jobs/{j['id']}/start", json={"version": j["version"]})).json()
    j = await settle(admin, j["id"])
    r = await admin.post(f"/api/core/jobs/{j['id']}/steps/transfer_plan/complete", json={"data": {}})
    assert r.status_code == 200, r.text  # pinned: reopen would now refuse (job_has_work)
    r = await admin.post(f"/api/core/jobs/{j['id']}/cancel", json={"reason": "kế hoạch chốt không còn đúng"})
    assert r.status_code == 200, r.text
    assert (await admin.post(f"/api/core/jobs/{j['id']}/reopen", json={"reason": "thử"})).status_code == 409
    again = await admin.post("/api/lifecycle/jobs", json={"request_id": "9103"})
    assert again.status_code == 201, again.text
    new = again.json()
    assert new["id"] != j["id"] and new["status"] == "draft"
    assert (await admin.post("/api/lifecycle/jobs", json={"request_id": "9103"})).json()["id"] == new["id"]
    new = (await admin.post(f"/api/core/jobs/{new['id']}/start", json={"version": new["version"]})).json()
    new = await settle(admin, new["id"])
    assert (
        step(new, "transfer_plan")["status"] == "waiting"
        and not step(new, "transfer_plan")["output"]["pinned"]
    )
    async with get_sessionmaker()() as s:  # transfer_env only cleans the first job: drop the second
        await s.execute(delete(Job).where(Job.id == uuid.UUID(new["id"])))
        await s.commit()
