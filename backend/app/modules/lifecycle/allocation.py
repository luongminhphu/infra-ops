"""Device allocation for onboard requests: band → standard → reuse In Store stock or buy new.

Pure functions (no I/O) so the rules are unit-tested; the API feeds them the latest asset snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.modules.lifecycle.parsing import fold
from app.modules.lifecycle.settings import StandardItem


@dataclass
class Need:
    request_id: str
    effective_date: date | None
    band: str | None
    picked: list[dict[str, Any]] = field(default_factory=list)  # assets already chosen on the job draft
    department: str | None = None


@dataclass
class Line:
    label: str
    quantity: int
    catalog_name: str
    picked: list[dict[str, Any]] = field(default_factory=list)
    reuse: list[dict[str, Any]] = field(default_factory=list)
    buy: int = 0


def norm_band(band: str | None) -> str:
    return fold(band or "").replace(" ", "")


def items_for(
    band: str | None, standard: list[StandardItem], department: str | None = None
) -> list[StandardItem]:
    """The lines a band receives. Matrix rows limited to departments win over the general row of the same band
    (e.g. "Band 4–6, IT" over "Band 1–6"); the "other bands" row only covers bands no other row lists."""
    b, dept = norm_band(band), fold(department or "")

    def band_ok(i: StandardItem) -> bool:
        if i.bands:
            return b in {norm_band(x) for x in i.bands}
        return b not in {norm_band(x) for x in i.except_bands}

    def dept_ok(i: StandardItem) -> bool:
        return not i.departments or dept in {fold(d) for d in i.departments}

    hits = [i for i in standard if band_ok(i) and dept_ok(i)]
    if any(i.departments for i in hits):
        return [i for i in hits if i.departments or not i.level]
    return hits


def matches(asset: dict[str, Any], item: StandardItem) -> bool:
    if fold(asset.get("product_type") or "") not in {fold(t) for t in item.product_types}:
        return False
    text = fold(f"{asset.get('product') or ''} {asset.get('name') or ''}")
    return not item.product_contains or fold(item.product_contains) in text


def returned_first(stock: list[dict[str, Any]], returned: set[str]) -> list[dict[str, Any]]:
    """Stock in suggestion order: returned (thu hồi) assets before new ones, so used devices in good shape go
    out first and new stock is kept. Each asset is tagged with ``returned``."""
    tagged = [{**a, "returned": a["id"] in returned} for a in stock]
    return sorted(
        tagged, key=lambda a: not a["returned"]
    )  # stable: keeps the snapshot order within each group


def allocate(
    needs: list[Need], standard: list[StandardItem], stock: list[dict[str, Any]]
) -> dict[str, list[Line]]:
    """Earliest effective date first: assets picked on a job draft count first, then free In Store
    assets matching the line (each asset used once), and the rest must be bought.

    Requests without a band get no lines (the caller shows "missing band")."""
    reserved = {a["id"] for n in needs for a in n.picked}
    free = [a for a in stock if a["id"] not in reserved]
    out: dict[str, list[Line]] = {}
    for n in sorted(needs, key=lambda n: (n.effective_date or date.max, n.request_id)):
        if not norm_band(n.band):
            out[n.request_id] = []
            continue
        picked = list(n.picked)
        lines = []
        for item in items_for(n.band, standard, n.department):
            line = Line(item.label, item.quantity, item.catalog_name)
            for pool, dest in ((picked, line.picked), (free, line.reuse)):
                for a in [a for a in pool if matches(a, item)][
                    : item.quantity - len(line.picked) - len(line.reuse)
                ]:
                    pool.remove(a)
                    dest.append(a)
            line.buy = item.quantity - len(line.picked) - len(line.reuse)
            lines.append(line)
        out[n.request_id] = lines
    return out
