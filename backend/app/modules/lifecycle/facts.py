"""Employee data a Playbook check can test (e.g. "monitors in use < monitors allowed for the band").

Read from the latest reconciliation and the RTR requests – never live from SDP/BioStar inside a request –
and None when unknown, so a check reports missing data instead of guessing."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.modules.manifest import FactSource, FactValue
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


async def _request_fields(session: AsyncSession, emp: str) -> tuple[dict[str, Any], str | None, str]:
    """Fields of the employee's latest RTR request, the band IT set by hand, and where it came from."""
    r = await session.scalar(
        select(LifecycleRequest)
        .where(LifecycleRequest.employee_id == emp)
        .order_by(LifecycleRequest.created_at.desc().nulls_last())
        .limit(1)
    )
    if r is None:
        return {}, None, ""
    return r.fields or {}, r.band_override, f"Request RTR #{r.display_id or r.id}"


async def level(session: AsyncSession, emp: str, _: str) -> FactValue | None:
    """The allocation-matrix row (cấp bậc) the employee falls in, from the job title."""
    settings = await load_settings(session)
    if settings.allocation_matrix is None:
        return None
    f, chosen, source = await _request_fields(session, emp)
    lv, why = settings.allocation_matrix.level_for(
        f.get("job_title"), f.get("band"), f.get("department"), chosen
    )
    return FactValue(lv.name, f"{source}: {why}") if lv else None


async def job_title(session: AsyncSession, emp: str, _: str) -> FactValue | None:
    f, _, source = await _request_fields(session, emp)
    return FactValue(f["job_title"], source) if f.get("job_title") else None


async def standard_quantity(session: AsyncSession, emp: str, product_type: str) -> FactValue | None:
    """How many of that product type the allocation standard gives the employee (matrix row from the job title,
    or the band without a matrix)."""
    settings = await load_settings(session)
    f, chosen, source = await _request_fields(session, emp)
    band, band_source = await _band(session, emp)
    items, lv, why = settings.items_for_employee(f.get("job_title"), band, f.get("department"), chosen)
    if items is None:
        return None
    qty = sum(i.quantity for i in items if fold(product_type) in {fold(t) for t in i.product_types})
    basis = f"cấp bậc {lv} ({why}, {source})" if lv else f"band {band} ({band_source})"
    if lv and band:
        basis += f" – band {band}"
    return FactValue(qty, f"Tiêu chuẩn cấp phát – {basis}: {qty} {product_type}")


FACTS = [
    FactSource("lifecycle.band", "Band của nhân viên", "text", band),
    FactSource("lifecycle.job_title", "Chức danh (request RTR)", "text", job_title),
    FactSource("lifecycle.level", "Cấp bậc theo ma trận cấp phát", "text", level),
    FactSource("lifecycle.department", "Phòng ban", "text", department),
    FactSource("lifecycle.assets_in_use", "Số tài sản đang dùng", "number", assets_in_use,
               param_label="Loại tài sản SDP", param_hint="Ví dụ: Monitor, Laptop, Desktop"),
    FactSource("lifecycle.standard_quantity", "Số lượng theo tiêu chuẩn band", "number", standard_quantity,
               param_label="Loại tài sản SDP", param_hint="Ví dụ: Monitor"),
]  # fmt: skip
