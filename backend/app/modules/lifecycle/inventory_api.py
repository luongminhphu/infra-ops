"""Stock & allocation: what each upcoming onboard needs (by band), what the In Store stock covers
and what must be bought (then proposed as a purchase request in the workload module)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record
from app.core.auth.deps import CurrentUser, require
from app.core.db import get_session
from app.core.errors import NotFound
from app.core.settings.service import load_settings
from app.core.workflow.models import Job
from app.modules.lifecycle.allocation import Line, Need, allocate, matches
from app.modules.lifecycle.models import LifecycleRequest, Snapshot
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import MODULE_ID, SETTINGS_KEY, LifecycleSettings

router = APIRouter(tags=["lifecycle-inventory"])
view = require("lifecycle.job.view")
execute = require("lifecycle.job.execute")
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def band_of(req: LifecycleRequest) -> tuple[str | None, str | None]:
    if req.band_override:
        return req.band_override, "override"
    parsed = (req.fields or {}).get("band")
    return (parsed, "rtr") if parsed else (None, None)


def _asset(a: dict[str, Any]) -> dict[str, Any]:
    return {k: a.get(k) for k in ("id", "name", "asset_tag", "product", "product_type")}


def _line(line: Line) -> dict[str, Any]:
    return {
        "label": line.label,
        "quantity": line.quantity,
        "catalog_name": line.catalog_name,
        "picked": [_asset(a) for a in line.picked],
        "reuse": [_asset(a) for a in line.reuse],
        "buy": line.buy,
    }


@router.get("/allocation")
async def allocation(
    _: CurrentUser = Depends(view), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = await load_settings(session, MODULE_ID, SETTINGS_KEY, LifecycleSettings)
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

    since = datetime.now(TZ).date() - timedelta(days=settings.allocation_past_days)
    closed = {s.lower() for s in settings.sdp_closed_statuses}
    reqs = [
        r
        for r in (
            await session.scalars(
                select(LifecycleRequest)
                .where(LifecycleRequest.kind == "onboard", LifecycleRequest.effective_date >= since)
                .order_by(LifecycleRequest.effective_date)
            )
        ).all()
        if (r.status or "").lower() not in closed
    ]
    jobs: dict[str, Job] = {}
    if reqs:
        for j in (
            await session.scalars(
                select(Job)
                .where(Job.workflow_key == "lifecycle.onboard", Job.external_ref.in_([r.id for r in reqs]))
                .order_by(Job.created_at)
            )
        ).all():
            if j.status != "cancelled":
                jobs[j.external_ref] = j  # type: ignore[index]
    reqs = [r for r in reqs if not (r.id in jobs and jobs[r.id].status == "done")]

    needs = []
    for r in reqs:
        job = jobs.get(r.id)
        picked = [{**a, **assets.get(a["id"], {})} for a in (job.params.get("assets", []) if job else [])]
        needs.append(Need(r.id, r.effective_date, band_of(r)[0], picked))
    plan = allocate(needs, settings.allocation_standard, stock)

    summary = []
    for item in settings.allocation_standard:
        lines = [ln for lines in plan.values() for ln in lines if ln.label == item.label]
        summary.append(
            {
                "label": item.label,
                "bands": item.bands,
                "product_types": item.product_types,
                "in_store": sum(matches(a, item) for a in stock),
                "planned": sum(len(ln.picked) + len(ln.reuse) for ln in lines),
                "to_buy": sum(ln.buy for ln in lines),
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
                "job": {"id": str(job.id), "status": job.status, "version": job.version} if job else None,
                "status": "no_band"
                if band is None
                else ("buy" if any(ln.buy for ln in lines) else "covered"),
                "lines": [_line(ln) for ln in lines],
            }
        )
    bands = sorted({b for i in settings.allocation_standard for b in i.bands})
    return {"as_of": run.finished_at if run else None, "stock": summary, "requests": out, "bands": bands}


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
