"""Employee data a Playbook check can test (e.g. "monitors in use < monitors allowed for the band").

Read from the latest reconciliation and the RTR requests – never live from SDP/BioStar inside a request –
and None when unknown, so a check reports missing data instead of guessing."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import FactSource, FactValue
from app.modules.lifecycle.allocation import items_for
from app.modules.lifecycle.models import LifecycleRequest, ReconRow, Snapshot
from app.modules.lifecycle.parsing import fold
from app.modules.lifecycle.queries import latest_run
from app.modules.lifecycle.settings import load as load_settings


async def _row(session: AsyncSession, emp: str) -> tuple[Any, dict[str, Any] | None]:
    run = await latest_run(session)
    if run is None:
        return None, None
    row = await session.scalar(select(ReconRow).where(ReconRow.run_id == run.id, ReconRow.employee_id == emp))
    return run, (row.data | {"department": row.department}) if row else None


async def _band(session: AsyncSession, emp: str) -> tuple[str | None, str]:
    value, source, _ = await _band_dept(session, emp)
    return value, source


async def _band_dept(session: AsyncSession, emp: str) -> tuple[str | None, str, str | None]:
    reqs = (
        await session.scalars(
            select(LifecycleRequest)
            .where(LifecycleRequest.employee_id == emp)
            .order_by(LifecycleRequest.created_at.desc().nulls_last())
        )
    ).all()
    for r in reqs:
        band = r.band_override or (r.fields or {}).get("band")
        if band:
            return (
                str(band),
                f"Request RTR #{r.display_id or r.id}" + (" (nhập tay)" if r.band_override else ""),
                (r.fields or {}).get("department"),
            )
    return None, "", None


async def band(session: AsyncSession, emp: str, _: str) -> FactValue | None:
    value, source = await _band(session, emp)
    return FactValue(value, source) if value else None


async def department(session: AsyncSession, emp: str, _: str) -> FactValue | None:
    _, row = await _row(session, emp)
    if not row or not row.get("department"):
        return None
    return FactValue(row["department"], "Đối soát gần nhất (SDP)")


async def assets_in_use(session: AsyncSession, emp: str, product_type: str) -> FactValue | None:
    """In-use assets of that SDP product type assigned to the employee in the latest reconciliation."""
    run, row = await _row(session, emp)
    if run is None or row is None or row.get("cells", {}).get("assets", {}).get("status") == "skipped":
        return None
    ids = row.get("asset_ids") or []
    assets: list[dict[str, Any]] = []
    if ids:
        stmt = select(Snapshot.data).where(
            Snapshot.run_id == run.id, Snapshot.system == "sdp_asset", Snapshot.external_id.in_(ids)
        )
        assets = list((await session.scalars(stmt)).all())
    settings = await load_settings(session)
    same = [a for a in assets if fold(a.get("product_type") or "") == fold(product_type)
            and a.get("state") == settings.asset_state_in_use]  # fmt: skip
    names = ", ".join(f"{a.get('name')} ({a.get('asset_tag') or '—'})" for a in same[:4])
    return FactValue(len(same), f"SDP: {len(same)} {product_type}" + (f" – {names}" if names else ""))


async def standard_quantity(session: AsyncSession, emp: str, product_type: str) -> FactValue | None:
    """How many of that product type the allocation standard gives the employee's band."""
    value, source, dept = await _band_dept(session, emp)
    if not value:
        return None
    settings = await load_settings(session)
    lines = [i for i in items_for(value, settings.allocation_standard, dept)
             if fold(product_type) in {fold(t) for t in i.product_types}]  # fmt: skip
    qty = sum(i.quantity for i in lines)
    return FactValue(qty, f"Tiêu chuẩn cấp phát band {value}: {qty} {product_type} ({source})")


FACTS = [
    FactSource("lifecycle.band", "Band của nhân viên", "text", band),
    FactSource("lifecycle.department", "Phòng ban", "text", department),
    FactSource("lifecycle.assets_in_use", "Số tài sản đang dùng", "number", assets_in_use,
               param_label="Loại tài sản SDP", param_hint="Ví dụ: Monitor, Laptop, Desktop"),
    FactSource("lifecycle.standard_quantity", "Số lượng theo tiêu chuẩn band", "number", standard_quantity,
               param_label="Loại tài sản SDP", param_hint="Ví dụ: Monitor"),
]  # fmt: skip
