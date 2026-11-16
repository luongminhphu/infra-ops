from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.lifecycle.queries import filtered_rows, latest_run, run_out
from app.modules.lifecycle.reconcile import RULES


async def reconciliation_summary(session: AsyncSession) -> dict[str, Any]:
    run = await latest_run(session)
    if run is None:
        return {"run": None}
    _, facets = await filtered_rows(session, run)
    top = sorted(facets["rules"].items(), key=lambda kv: -kv[1])[:5]
    return {
        "run": run_out(run),
        "severity": facets["severity"],
        "employees": facets["total_rows"],
        "top_rules": [{"rule": k, "title": RULES[k][2], "severity": RULES[k][0], "count": v} for k, v in top],
    }
