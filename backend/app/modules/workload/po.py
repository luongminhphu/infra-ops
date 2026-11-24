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
from markupsafe import Markup, escape
from openpyxl import Workbook
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage.service import read_file
from app.modules.workload.models import (
    CatalogItem,
    PurchaseEvidence,
    PurchaseOrder,
    PurchaseQuote,
    PurchaseRequest,
)
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
    amount = {p.id: (p.quote_price or price or 0) * p.quantity for p, price in pending}
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
    """Frozen price (closed PO), else the lowest vendor quote, else the price list."""
    if p.unit_price is not None:
        return p.unit_price
    if p.quote_price is not None:
        return p.quote_price
    return c.unit_price if c else None


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


Quotes = dict[uuid.UUID, list[PurchaseQuote]]


def quote_summary(qs: list[PurchaseQuote], chosen: int | None) -> str:
    """'FPT 25.900.000 đ (chọn) · Phong Vũ 26.400.000 đ · An Phát: hết hàng'"""
    return " · ".join(
        f"{q.vendor}: hết hàng" if q.out_of_stock
        else f"{q.vendor} {vnd(q.unit_price)}" + (" (chọn – thấp nhất)" if q.unit_price == chosen else "")
        for q in qs
    )  # fmt: skip


def items_html(items: list[tuple[PurchaseRequest, CatalogItem | None]], quotes: Quotes) -> Markup:
    td = 'style="border:1px solid #c8ced8;padding:4px 8px;vertical-align:top"'
    head = "".join(
        f"<th {td.replace('vertical-align:top', 'background:#eef2f8;text-align:left')}>{h}</th>"
        for h in ("STT", "Thiết bị", "SL", "Đơn giá", "Thành tiền", "Báo giá so sánh", "Người dùng / Request")
    )
    rows = []
    for i, (p, c) in enumerate(items, 1):
        price = price_of(p, c)
        qs = quotes.get(p.id, [])
        ref = f"#{p.ticket_display_id}" if p.ticket_display_id else (p.source_ref or "")
        cells = [
            str(i),
            escape(c.name if c else p.item),
            str(p.quantity),
            vnd(price),
            f"<b>{vnd((price or 0) * p.quantity)}</b>",
            escape(quote_summary(qs, price)) if qs else "Theo bảng giá",
            escape(" ".join(x for x in (p.employee or "", ref) if x)),
        ]
        rows.append("<tr>" + "".join(f"<td {td}>{x}</td>" for x in cells) + "</tr>")
    return Markup(  # noqa: S704 – every user-supplied cell above is escape()d; the rest are numbers
        f'<table style="border-collapse:collapse;font-size:13px"><tr>{head}</tr>{"".join(rows)}</table>'
    )


def render_email(
    settings: WorkloadSettings,
    po: PurchaseOrder,
    items: list[tuple[PurchaseRequest, CatalogItem | None]],
    quotes: Quotes | None = None,
) -> tuple[str, str, str]:
    """Subject, plain-text body and HTML body (the item table with the compared quotes)."""
    quotes = quotes or {}
    lines = []
    for i, (p, c) in enumerate(items, 1):
        price = price_of(p, c)
        lines.append(
            f"{i}. {c.name if c else p.item} × {p.quantity} – {vnd(price)}/cái = {vnd((price or 0) * p.quantity)}"
            + (f" (request #{p.ticket_display_id})" if p.ticket_display_id else "")
        )
        if quotes.get(p.id):
            lines.append(f"   Báo giá: {quote_summary(quotes[p.id], price)}")
    ctx = {
        "po_number": po.number or "PO nháp",
        "date": (po.closed_at or datetime.now(UTC)).astimezone(TZ).strftime("%d/%m/%Y"),
        "total": vnd(po.total),
        "limit": vnd(po.limit),
        "item_count": len(items),
        "items_text": "\n".join(lines),
        "items_table": "\n".join(lines),
        "items": [
            {
                "name": c.name if c else p.item,
                "quantity": p.quantity,
                "unit_price": vnd(price_of(p, c)),
                "amount": vnd((price_of(p, c) or 0) * p.quantity),
                "request": p.ticket_display_id or p.source_ref,
                "quotes": quote_summary(quotes.get(p.id, []), price_of(p, c)),
            }
            for p, c in items
        ],
        "closed_by": po.closed_by or "",
    }
    env = SandboxedEnvironment(autoescape=False, keep_trailing_newline=True)
    subject = env.from_string(settings.po_email_subject).render(ctx).replace("\n", " ").strip()
    text = env.from_string(settings.po_email_body).render(ctx)
    # HTML: same template, autoescaped; the item list (either variable) becomes the comparison table.
    table = items_html(items, quotes)
    html_env = SandboxedEnvironment(autoescape=True, keep_trailing_newline=True)
    html_body = html_env.from_string(settings.po_email_body).render(
        {**ctx, "items_text": table, "items_table": table}
    )
    html = f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">{html_body.replace(chr(10), "<br>" + chr(10))}</div>'
    return subject, text, html


async def load_quotes(session: AsyncSession, ids: list[uuid.UUID]) -> Quotes:
    out: Quotes = {}
    if ids:
        rows = (
            await session.scalars(
                select(PurchaseQuote)
                .where(PurchaseQuote.purchase_id.in_(ids))
                .order_by(PurchaseQuote.out_of_stock, PurchaseQuote.unit_price)
            )
        ).all()
        for q in rows:
            out.setdefault(q.purchase_id, []).append(q)
    return out


async def build_eml(session: AsyncSession, settings: WorkloadSettings, po: PurchaseOrder) -> bytes:
    """Unsent draft (X-Unsent) with the detail workbook and the Function Head approvals attached."""
    items = await po_items(session, po)
    quotes = await load_quotes(session, [p.id for p, _ in items])
    subject, body, html = render_email(settings, po, items, quotes)
    msg = EmailMessage()
    msg["Subject"] = subject
    if settings.po_email_to:
        msg["To"] = ", ".join(settings.po_email_to)
    if settings.po_email_cc:
        msg["Cc"] = ", ".join(settings.po_email_cc)
    msg["X-Unsent"] = "1"
    msg.set_content(body)
    msg.add_alternative(html, subtype="html")
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
    for qs in quotes.values():
        for q in qs:
            _, data = await read_file(session, q.file_id)
            main, sub = q.mime.split("/", 1)
            kind = "het-hang" if q.out_of_stock else "bao-gia"
            msg.add_attachment(
                data, maintype=main, subtype=sub, filename=f"{kind}-{display[q.purchase_id]}-{q.filename}"
            )
    return bytes(msg)
