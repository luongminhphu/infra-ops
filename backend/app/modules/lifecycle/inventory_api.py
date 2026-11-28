"""Stock & allocation: what each upcoming onboard needs (by band), what the In Store stock covers
and what must be bought (then proposed as a purchase request in the workload module)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.db import get_session
from app.core.errors import NotFound
from app.core.timeutil import today_vn
from app.core.workflow.api import check_job_scope
from app.core.workflow.models import Job
from app.modules.lifecycle.allocation import Line, Need, allocate, matches, returned_first
from app.modules.lifecycle.models import AssetReturn, LifecycleRequest, Snapshot
from app.modules.lifecycle.parsing import fold
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import FIELD_TITLES, MODULE_ID
from app.modules.lifecycle.settings import load as load_module_settings

router = APIRouter(tags=["lifecycle-inventory"])
view = require("lifecycle.job.view")
execute = require("lifecycle.job.execute")


def band_of(req: LifecycleRequest) -> tuple[str | None, str | None]:
    if req.band_override:
        return req.band_override, "override"
    parsed = (req.fields or {}).get("band")
    return (parsed, "rtr") if parsed else (None, None)


def _asset(a: dict[str, Any]) -> dict[str, Any]:
    return {k: a.get(k) for k in ("id", "name", "asset_tag", "product", "product_type", "returned")}


def _line(line: Line) -> dict[str, Any]:
    return {
        "label": line.label,
        "quantity": line.quantity,
        "catalog_name": line.catalog_name,
        "picked": [_asset(a) for a in line.picked],
        "reuse": [_asset(a) for a in line.reuse],
        "buy": line.buy,
        "wait": line.wait,
    }


@router.get("/allocation")
async def allocation(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    return await build_plan(session)


# Friendlier card titles for common SDP product types outside the allocation standard.
OTHER_LABELS = {"workstation": "PC", "desktop": "PC"}


async def build_plan(session: AsyncSession) -> dict[str, Any]:
    """Stock summary and per-request allocation (reuse / buy) for upcoming onboards."""
    settings = await load_module_settings(session)
    run = await latest_run(session)
    assets: dict[str, dict[str, Any]] = {}
    if run is not None:
        for s in (
            await session.scalars(
                select(Snapshot).where(Snapshot.run_id == run.id, Snapshot.system == "sdp_asset")
            )
        ).all():
            assets[s.data["id"]] = s.data
    stock = [
        a for a in assets.values() if a.get("state") == settings.asset_state_in_store and not a.get("user_id")
    ]
    # Returned (thu hồi) devices are suggested before new stock.
    stock = returned_first(stock, set((await session.scalars(select(AssetReturn.asset_id))).all()))

    since = today_vn() - timedelta(days=settings.allocation_past_days)
    reqs = [
        r
        for r in (
            await session.scalars(
                select(LifecycleRequest)
                .where(LifecycleRequest.kind == "onboard", LifecycleRequest.effective_date >= since)
                .order_by(LifecycleRequest.effective_date)
            )
        ).all()
        if not settings.is_closed(r.status)
    ]
    jobs: dict[str, Job] = {}
    if reqs:
        for j in (
            await session.scalars(
                select(Job)
                .where(Job.workflow_key == "lifecycle.onboard", Job.external_ref.in_([r.id for r in reqs]))
                .order_by(Job.created_at)
                .options(
                    raiseload(Job.steps), raiseload(Job.evidence), raiseload(Job.assignments)
                )  # only status / params are read
            )
        ).all():
            if j.status != "cancelled":
                jobs[j.external_ref] = j  # type: ignore[index]
    reqs = [r for r in reqs if not (r.id in jobs and jobs[r.id].status == "completed")]

    needs = []
    levels: dict[str, tuple[str | None, str]] = {}  # request -> (matrix row, why)
    for r in reqs:
        job = jobs.get(r.id)
        picked = [{**a, **assets.get(a["id"], {})} for a in (job.params.get("assets", []) if job else [])]
        f = r.fields or {}
        items, level, why = settings.items_for_employee(
            f.get("job_title"), f.get("band"), f.get("department"), r.band_override
        )
        levels[r.id] = (level, why)
        needs.append(Need(r.id, r.effective_date, band_of(r)[0], picked, items))
    plan = allocate(needs, settings.allocation_standard, stock)

    # Which upcoming onboard each stock asset is set aside for: chosen on its job draft, or only suggested.
    reserved: dict[str, dict[str, Any]] = {}
    by_id = {r.id: r for r in reqs}
    for rid, lines in plan.items():
        for ln in lines:
            for a, chosen in [*((a, True) for a in ln.picked), *((a, False) for a in ln.reuse)]:
                r = by_id[rid]
                reserved.setdefault(
                    a["id"], {"name": r.full_name, "display_id": r.display_id or r.id, "chosen": chosen}
                )
    in_stock = {a["id"] for a in stock}
    summary = []
    # One stock row per label: matrix rows can give the same device under the same name.
    for item in {i.label: i for i in settings.allocation_standard}.values():
        lines = [ln for lines in plan.values() for ln in lines if ln.label == item.label]
        summary.append(
            {
                "label": item.label,
                "bands": item.bands,
                "product_types": item.product_types,
                "in_store": sum(matches(a, item) for a in stock),
                # Only assets still in stock: in_store = free + planned holds exactly.
                "planned": sum(a["id"] in in_stock for ln in lines for a in [*ln.picked, *ln.reuse]),
                "chosen": sum(a["id"] in in_stock for ln in lines for a in ln.picked),
                "to_buy": sum(ln.buy for ln in lines),
                "waiting": sum(ln.wait for ln in lines),  # reuse-only lines with no returned device free
                # Stock detail: every matching In Store asset and who it is set aside for.
                "items": sorted(
                    (
                        {**_asset(a), "serial": a.get("serial"), "reserved_for": reserved.get(a["id"])}
                        for a in stock
                        if matches(a, item)
                    ),
                    key=lambda x: ((x["product"] or ""), (x["name"] or "")),
                ),
                "standard": True,
            }
        )
    # In Store devices outside the allocation standard (PC, UPS, …): counted per SDP product type.
    covered = {fold(t) for i in settings.allocation_standard for t in i.product_types}
    others: dict[str, list[dict[str, Any]]] = {}
    for a in stock:
        ptype = a.get("product_type") or "Khác"
        if fold(ptype) not in covered:
            others.setdefault(ptype, []).append(a)
    for ptype, found in sorted(others.items(), key=lambda kv: kv[0].lower()):
        summary.append(
            {
                "label": OTHER_LABELS.get(fold(ptype), ptype),
                "bands": [],
                "product_types": [ptype],
                "in_store": len(found),
                "planned": 0,
                "chosen": 0,
                "to_buy": 0,
                "waiting": 0,
                "items": sorted(
                    ({**_asset(a), "serial": a.get("serial"), "reserved_for": None} for a in found),
                    key=lambda x: ((x["product"] or ""), (x["name"] or "")),
                ),
                "standard": False,
            }
        )
    out = []
    for r in reqs:
        band, source = band_of(r)
        lines = plan[r.id]
        job = jobs.get(r.id)
        out.append(
            {
                "id": r.id,
                "display_id": r.display_id,
                "full_name": r.full_name,
                "employee_id": r.employee_id,
                "department": (r.fields or {}).get("department"),
                "job_title": (r.fields or {}).get("job_title"),
                "effective_date": r.effective_date,
                "band": band,
                "band_source": source,
                "level": levels[r.id][0],
                "level_reason": levels[r.id][1],
                "job": {"id": str(job.id), "status": job.status, "version": job.version} if job else None,
                "status": "no_band"
                if (levels[r.id][0] is None if settings.allocation_matrix else band is None)
                else (
                    "buy"
                    if any(ln.buy for ln in lines)
                    else ("wait" if any(ln.wait for ln in lines) else "covered")
                ),
                "lines": [_line(ln) for ln in lines],
            }
        )
    # What IT can pick by hand for a request: matrix rows, or the bands of hand-written lines.
    bands = (
        [lv.name for lv in settings.allocation_matrix.levels]
        if settings.allocation_matrix
        else sorted({b for i in settings.allocation_standard for b in i.bands})
    )
    # Hires without a band have no computable need: the shortage excludes them, so say how many.
    no_band = sum(r["status"] == "no_band" for r in out)
    return {
        "as_of": run.finished_at if run else None,
        "stock": summary,
        "requests": out,
        "bands": bands,
        "matrix": settings.allocation_matrix is not None,
        "no_band": no_band,
    }


class BandIn(BaseModel):
    band: str | None = Field(None, max_length=32)


@router.put("/requests/{request_id}/band")
async def set_band(
    request_id: str,
    body: BandIn,
    user: CurrentUser = Depends(execute),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    req = await session.get(LifecycleRequest, request_id, with_for_update=True)
    if req is None:
        raise NotFound("Không tìm thấy request.")
    before = req.band_override
    req.band_override = (body.band or "").strip() or None
    await record(
        session,
        actor=user.actor,
        module_id=MODULE_ID,
        action="lifecycle.band_set",
        target_type="request",
        target_id=req.id,
        target_label=f"{req.full_name or ''} ({req.employee_id or '—'})",
        before={"band": before},
        after={"band": req.band_override},
    )
    await session.commit()
    band, source = band_of(req)
    return {"band": band, "band_source": source}


@router.get("/jobs/{job_id}/prepare")
async def prepare(
    job_id: uuid.UUID, user: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Everything already known for a job's preparation wizard, so the operator confirms instead of typing:
    the RTR details, the SDP user matched by employee ID and the device allocation (reuse / buy)."""
    job = await session.get(Job, job_id)
    if job is None or job.module_id != MODULE_ID:
        raise NotFound("Không tìm thấy Job.")
    check_job_scope(user, job)
    req = await session.get(LifecycleRequest, job.external_ref) if job.external_ref else None
    if req is None:
        raise NotFound("Không tìm thấy request RTR của Job.")
    band, band_source = band_of(req)
    details = [
        {"key": k, "label": title, "value": (req.fields or {}).get(k)}
        for k, title in FIELD_TITLES.items()
        if k not in ("change_type", "full_name", "employee_id", "effective_date", "band")
        and (req.fields or {}).get(k)
    ]
    sdp_user = None
    run = await latest_run(session)
    if run is not None and req.employee_id:
        snap = await session.scalar(
            select(Snapshot).where(
                Snapshot.run_id == run.id,
                Snapshot.system == "sdp_user",
                Snapshot.employee_id == req.employee_id,
            )
        )
        if snap is not None:
            sdp_user = {k: snap.data.get(k) for k in ("id", "name", "email", "employee_id")}
    plan = await build_plan(session) if req.kind == "onboard" else None
    entry = next((r for r in plan["requests"] if r["id"] == req.id), None) if plan else None
    return {
        "request": {
            "id": req.id,
            "display_id": req.display_id,
            "kind": req.kind,
            "full_name": req.full_name,
            "employee_id": req.employee_id,
            "effective_date": req.effective_date,
            "band": band,
            "band_source": band_source,
            "details": details,
        },
        "sdp_user": sdp_user,
        "allocation": entry,
        "as_of": run.finished_at if run else None,
        "retained_ou_configured": bool((await load_module_settings(session)).ad_retained_ou),
    }
