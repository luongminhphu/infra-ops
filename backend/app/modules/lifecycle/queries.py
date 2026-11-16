"""Read helpers over the latest reconciliation run (shared by API, export and dashboard)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.lifecycle.models import ReconRow, SyncRun
from app.modules.lifecycle.parsing import fold


async def latest_run(session: AsyncSession) -> SyncRun | None:
    """Latest finished run that produced data (success or partial)."""
    return await session.scalar(
        select(SyncRun)
        .where(SyncRun.finished_at.is_not(None), SyncRun.status.in_(["success", "partial"]))
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    )


def run_out(run: SyncRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": str(run.id),
        "trigger": run.trigger,
        "requested_by": run.requested_by,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "stats": run.stats,
        "errors": run.errors,
        "notes_count": len(run.notes or []),
    }


async def filtered_rows(
    session: AsyncSession,
    run: SyncRun,
    *,
    q: str = "",
    only_issues: bool = False,
    severity: str = "",
    rule: str = "",
    kind: str = "",
    department: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = (await session.scalars(select(ReconRow).where(ReconRow.run_id == run.id))).all()
    rule_counts: Counter[str] = Counter()
    departments: Counter[str] = Counter()
    for r in rows:
        for i in r.data.get("issues", []):
            if not i.get("acked"):
                rule_counts[i["rule"]] += 1
        if r.department:
            departments[r.department] += 1
    needle = fold(q) if q else ""
    out = []
    for r in rows:
        data = r.data
        if needle and needle not in fold(f"{r.employee_id} {r.full_name}"):
            continue
        if only_issues and r.issue_count == 0:
            continue
        if severity and r.max_severity != severity:
            continue
        if rule and not any(i["rule"] == rule and not i.get("acked") for i in data.get("issues", [])):
            continue
        if kind and r.kind != kind:
            continue
        if department and r.department != department:
            continue
        out.append({**data, "issue_count": r.issue_count, "max_severity": r.max_severity})
    order = {"high": 0, "medium": 1, "low": 2, None: 3}
    out.sort(key=lambda d: (order.get(d["max_severity"], 3), d["employee_id"]))
    facets = {
        "rules": dict(rule_counts),
        "departments": sorted(departments),
        "severity": dict(Counter(r.max_severity for r in rows if r.max_severity)),
        "total_rows": len(rows),
    }
    return out, facets
