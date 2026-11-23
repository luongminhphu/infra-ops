"""Purchase orders: approved proposals packed into POs that stay within the value limit."""

from __future__ import annotations

import io
import uuid
from collections.abc import Hashable, Sequence
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

from jinja2.sandbox import SandboxedEnvironment
from openpyxl import Workbook
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage.service import read_file
from app.modules.workload.models import CatalogItem, PurchaseEvidence, PurchaseOrder, PurchaseRequest
from app.modules.workload.settings import WorkloadSettings

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
KIND_LABEL = {"replace": "Thay thế", "upgrade": "Nâng cấp", "repair": "Sửa chữa", "new": "Cấp mới"}


def pack[K: Hashable](items: Sequence[tuple[K, int]], limit: int) -> list[list[K]]:
    """First-fit in the given order: each item goes into the first PO with room, else a new PO.

    An item worth more than the limit on its own gets a PO of its own (flagged over limit)."""
    bins: list[list[Any]] = []  # [total, keys]
    for key, amount in items:
        for b in bins:
            if b[0] + amount <= limit:
                b[0] += amount
                b[1].append(key)
                break
        else:
            bins.append([amount, [key]])
    return [b[1] for b in bins]


def vnd(amount: int | None) -> str:
    return "—" if amount is None else f"{amount:,}".replace(",", ".") + " đ"


async def _lock(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('workload.po'))"))


async def repack(session: AsyncSession, settings: WorkloadSettings) -> None:
    """Rebuild the open POs from the approved proposals not yet in a closed PO (by approval time).

    Called after approvals, rejections, price changes and limit changes; closed POs never change."""
    await _lock(session)
    open_pos = list(
        (
            await session.scalars(
                select(PurchaseOrder).where(PurchaseOrder.status == "open").order_by(PurchaseOrder.created_at)
            )
        ).all()
    )
    open_ids = {p.id for p in open_pos}
    rows = (
        await session.execute(
            select(PurchaseRequest, CatalogItem.unit_price)
            .outerjoin(CatalogItem, CatalogItem.id == PurchaseRequest.catalog_item_id)
            .where(PurchaseRequest.status == "approved")
            .order_by(PurchaseRequest.approved_at, PurchaseRequest.created_at)
        )
    ).all()
    pending = [(p, price) for p, price in rows if p.po_id is None or p.po_id in open_ids]
    amount = {p.id: (price or 0) * p.quantity for p, price in pending}
    by_id = {p.id: p for p, _ in pending}
    groups = pack([(p.id, amount[p.id]) for p, _ in pending], settings.po_limit_vnd)
    while len(open_pos) < len(groups):
        po = PurchaseOrder(id=uuid.uuid4(), status="open", total=0, limit=settings.po_limit_vnd)
        session.add(po)
        open_pos.append(po)
    await session.flush()
    for po, keys in zip(open_pos, groups, strict=False):
        po.total, po.limit = sum(amount[k] for k in keys), settings.po_limit_vnd
        for k in keys:
            by_id[k].po_id = po.id
    for po in open_pos[len(groups) :]:
        await session.delete(po)
    await session.flush()


async def next_number(session: AsyncSession, now: datetime) -> str:
    year = now.astimezone(TZ).year
    count = await session.scalar(select(func.count()).where(PurchaseOrder.number.like(f"PO-{year}-%")))
    return f"PO-{year}-{(count or 0) + 1:03d}"


async def po_items(
    session: AsyncSession, po: PurchaseOrder
) -> list[tuple[PurchaseRequest, CatalogItem | None]]:
    rows = (
        await session.execute(
            select(PurchaseRequest, CatalogItem)
            .outerjoin(CatalogItem, CatalogItem.id == PurchaseRequest.catalog_item_id)
            .where(PurchaseRequest.po_id == po.id)
            .order_by(PurchaseRequest.approved_at, PurchaseRequest.created_at)
        )
    ).all()
    return [(p, c) for p, c in rows]


def price_of(p: PurchaseRequest, c: CatalogItem | None) -> int | None:
    return p.unit_price if p.unit_price is not None else (c.unit_price if c else None)


def items_workbook(items: list[tuple[PurchaseRequest, CatalogItem | None]], title: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(["STT", "Thiết bị", "Quy cách", "Loại", "Số lượng", "Đơn giá (đ)", "Thành tiền (đ)",
               "Request", "Người dùng", "Lý do", "Người đề xuất"])  # fmt: skip
    for i, (p, c) in enumerate(items, 1):
        price = price_of(p, c)
        ws.append(
            [
                i,
                c.name if c else p.item,
                c.spec if c else "",
                KIND_LABEL.get(p.kind, p.kind),
                p.quantity,
                price,
                (price or 0) * p.quantity,
                f"#{p.ticket_display_id}" if p.ticket_display_id else (p.source_ref or ""),
                p.employee or "",
                p.reason,
                p.created_by,
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def render_email(
    settings: WorkloadSettings, po: PurchaseOrder, items: list[tuple[PurchaseRequest, CatalogItem | None]]
) -> tuple[str, str]:
    lines = []
    for i, (p, c) in enumerate(items, 1):
        price = price_of(p, c)
        lines.append(
            f"{i}. {c.name if c else p.item} × {p.quantity} – {vnd(price)}/cái = {vnd((price or 0) * p.quantity)}"
            + (f" (request #{p.ticket_display_id})" if p.ticket_display_id else "")
        )
    ctx = {
        "po_number": po.number or "PO nháp",
        "date": (po.closed_at or datetime.now(UTC)).astimezone(TZ).strftime("%d/%m/%Y"),
        "total": vnd(po.total),
        "limit": vnd(po.limit),
        "item_count": len(items),
        "items_text": "\n".join(lines),
        "items": [
            {
                "name": c.name if c else p.item,
                "quantity": p.quantity,
                "unit_price": vnd(price_of(p, c)),
                "amount": vnd((price_of(p, c) or 0) * p.quantity),
                "request": p.ticket_display_id or p.source_ref,
            }
            for p, c in items
        ],
        "closed_by": po.closed_by or "",
    }
    env = SandboxedEnvironment(autoescape=False, keep_trailing_newline=True)
    subject = env.from_string(settings.po_email_subject).render(ctx).replace("\n", " ").strip()
    return subject, env.from_string(settings.po_email_body).render(ctx)


async def build_eml(session: AsyncSession, settings: WorkloadSettings, po: PurchaseOrder) -> bytes:
    """Unsent draft (X-Unsent) with the detail workbook and the Function Head approvals attached."""
    items = await po_items(session, po)
    subject, body = render_email(settings, po, items)
    msg = EmailMessage()
    msg["Subject"] = subject
    if settings.po_email_to:
        msg["To"] = ", ".join(settings.po_email_to)
    if settings.po_email_cc:
        msg["Cc"] = ", ".join(settings.po_email_cc)
    msg["X-Unsent"] = "1"
    msg.set_content(body)
    msg.add_attachment(
        items_workbook(items, po.number or "PO"),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{po.number or 'PO'}-chi-tiet.xlsx",
    )
    ids = [p.id for p, _ in items]
    evidence = (
        await session.scalars(
            select(PurchaseEvidence).where(PurchaseEvidence.purchase_id.in_(ids)).order_by(PurchaseEvidence.created_at)
        )
    ).all() if ids else []  # fmt: skip
    display = {p.id: p.ticket_display_id or p.source_ref or str(p.id)[:8] for p, _ in items}
    for ev in evidence:
        _, data = await read_file(session, ev.file_id)
        main, sub = ev.mime.split("/", 1)
        msg.add_attachment(
            data, maintype=main, subtype=sub, filename=f"duyet-{display[ev.purchase_id]}-{ev.filename}"
        )
    return bytes(msg)
