"""Scheduled digest of "Việc tiếp theo": e-mail per opted-in person, team totals to Teams.

Safety rules (CHALLENGES C-05, C-06):
* A log row (unique per person/channel/slot) is committed BEFORE sending. A row left ``pending`` by a crash is
  never sent again automatically: a missed mail is better than a duplicate.
* Each person gets only items they may see, computed with their own permissions at send time.
* An item is repeated only when its priority became more urgent (smaller) than the last one mailed or its title
  changed (C-29).
* Teams gets counts per priority only (current backlog, de-duplicated by key, C-30): no names, titles or money.
* A system failure before anything was handed to the relay (``smtp_auth``/``smtp_connect``/``smtp_tls``) marks the
  row ``aborted`` (claimable again next run) and stops the run (C-23). A timeout mid-send leaves the row ``pending``.
* Every eligible person is visited in id order; ``max_per_run`` only caps mails, so nobody starves (C-25).
"""

from __future__ import annotations

import html
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import Actor, record
from app.core.auth.deps import CurrentUser
from app.core.auth.models import User
from app.core.connections.service import open_connection
from app.core.connectors import smtp, teams
from app.core.connectors.base import Purpose
from app.core.dashboard.service import collect_next_actions
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.modules.registry import get_registry
from app.core.notifications.models import NotificationItem, NotificationPref, NotificationSent
from app.core.permissions import DASHBOARD_VIEW
from app.core.rbac.service import permissions_for_user
from app.core.settings.service import load_settings, save_settings
from app.core.timeutil import TZ, fmt_date

log = get_logger(__name__)

MODULE_ID, KEY = "core", "notifications"
WINDOW = timedelta(
    minutes=90
)  # a slot is served by any run starting within this time after it (claim dedupes)
LATE = timedelta(minutes=15)  # served later than this -> warning in the log
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PRIORITY_LABEL = {
    0: "Đang chờ bạn xử lý",
    1: "Quá hạn / khẩn",
    2: "Sắp đến hạn",
    3: "Cần biết",
    4: "Gợi ý",
}


class NotificationSettings(BaseModel):
    enabled: bool = False  # master switch: nothing is sent while off
    times: list[str] = Field(default_factory=lambda: ["08:00", "13:30"], max_length=6)  # HH:MM, VN time
    smtp_profile_id: uuid.UUID | None = None
    teams_profile_id: uuid.UUID | None = None
    max_per_run: int = Field(200, ge=1, le=2000)
    app_base_url: str = Field("", max_length=512, pattern=r"^(https?://\S+)?$")  # prefix of the links in mail

    @field_validator("times")
    @classmethod
    def _times(cls, v: list[str]) -> list[str]:
        if any(not _HHMM.match(t) for t in v):
            raise ValueError("Giờ phải có dạng HH:MM.")
        return sorted(set(v))


async def load(session: AsyncSession) -> NotificationSettings:
    return await load_settings(session, MODULE_ID, KEY, NotificationSettings)


async def save(session: AsyncSession, actor: Actor, data: dict[str, Any]) -> NotificationSettings:
    return await save_settings(session, actor, MODULE_ID, KEY, NotificationSettings, data)


async def get_pref(session: AsyncSession, user_id: uuid.UUID) -> bool:
    row = await session.get(NotificationPref, user_id)
    return bool(row and row.email_digest)


async def set_pref(session: AsyncSession, actor: Actor, email_digest: bool) -> bool:
    assert actor.user_id is not None
    before = await get_pref(session, actor.user_id)
    stmt = insert(NotificationPref).values(user_id=actor.user_id, email_digest=email_digest)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id"], set_={"email_digest": email_digest, "updated_at": datetime.now(UTC)}
        )
    )
    if before != email_digest:
        await record(
            session,
            actor=actor,
            module_id=MODULE_ID,
            action="notifications.pref_changed",
            target_type="user",
            target_id=str(actor.user_id),
            target_label=actor.upn,
            before={"email_digest": before},
            after={"email_digest": email_digest},
        )
    return email_digest


def due_slots(now: datetime, times: list[str]) -> list[str]:
    """Slots ("YYYY-MM-DDTHH:MM", VN time) that started within the last WINDOW (today or, across midnight, yesterday)."""
    local = now.astimezone(TZ)
    out: set[str] = set()
    for back in (0, 1):
        for t in times:
            h, m = int(t[:2]), int(t[3:])
            start = (local - timedelta(days=back)).replace(hour=h, minute=m, second=0, microsecond=0)
            if timedelta(0) <= local - start < WINDOW:
                out.add(start.strftime("%Y-%m-%dT%H:%M"))
    return sorted(out)


def _due_text(due: Any) -> str:
    if not due:
        return ""
    if isinstance(due, date):
        return fmt_date(due)
    try:
        return fmt_date(date.fromisoformat(str(due)[:10]))
    except ValueError:
        return ""


def _link(base: str, href: str | None) -> str:
    return base.rstrip("/") + href if base and href and href.startswith("/") else ""


def build_mail(
    display_name: str, items: list[dict[str, Any]], base_url: str, now: datetime
) -> tuple[str, str, str]:
    """(subject, plain text, html). Every user-controlled string is escaped in the HTML part."""
    stamp = now.astimezone(TZ)
    subject = f"infra-ops – Việc tiếp theo {fmt_date(stamp)} {stamp:%H:%M}"
    text = [f"Chào {display_name},", "", "Các việc cần bạn xử lý tiếp theo:", ""]
    rows: list[str] = []
    for i in items:
        due, url = _due_text(i.get("due")), _link(base_url, i.get("href"))
        label = PRIORITY_LABEL.get(int(i["priority"]), "")
        line = f"- [{label}] {i.get('title', '')}"
        line += f" – {i['detail']}" if i.get("detail") else ""
        line += f" (hạn {due})" if due else ""
        text.append(line + (f"\n  {url}" if url else ""))
        title = html.escape(str(i.get("title", "")))
        if url:
            title = f'<a href="{html.escape(url, quote=True)}">{title}</a>'
        rows.append(
            f"<li><strong>{html.escape(label)}</strong>: {title}"
            + (f" – {html.escape(str(i['detail']))}" if i.get("detail") else "")
            + (f" <em>(hạn {html.escape(due)})</em>" if due else "")
            + "</li>"
        )
    text += ["", "Thư tự động từ infra-ops. Tắt thư này ở mục Thông báo trong trang cá nhân."]
    body = (
        f"<p>Chào {html.escape(display_name)},</p><p>Các việc cần bạn xử lý tiếp theo:</p>"
        f"<ul>{''.join(rows)}</ul>"
        "<p><small>Thư tự động từ infra-ops. Tắt thư này ở mục Thông báo trong trang cá nhân.</small></p>"
    )
    return subject, "\n".join(text), body


def team_text(counts: dict[int, int], people: int, now: datetime) -> str:
    stamp = now.astimezone(TZ)
    lines = [f"Việc tiếp theo – {fmt_date(stamp)} {stamp:%H:%M} – {people} người nhận thư"]
    lines += [f"{PRIORITY_LABEL.get(p, p)}: {n}" for p, n in sorted(counts.items())]
    return "\n".join(lines)


def _k(i: dict[str, Any]) -> str:
    return str(i["key"])[:256]  # the column is 256 wide: compare and store the same truncated key


def _title(i: dict[str, Any]) -> str:
    return str(i.get("title", ""))[:512]


async def _recipient(session: AsyncSession, u: User) -> CurrentUser:
    perms = await permissions_for_user(session, u.id, get_registry())
    return CurrentUser(id=u.id, upn=u.upn, display_name=u.display_name, permissions=perms)


async def _fresh_items(
    session: AsyncSession, who: CurrentUser
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """(all current items, items never mailed / more urgent / retitled, every source answered)."""
    items, failed = await collect_next_actions(session, who)
    last = {
        r.item_key: (r.last_priority, r.last_title)
        for r in (
            await session.execute(
                select(
                    NotificationItem.item_key, NotificationItem.last_priority, NotificationItem.last_title
                ).where(NotificationItem.user_id == who.id)
            )
        ).all()
    }
    fresh = [
        i for i in items if _k(i) not in last or i["priority"] < last[_k(i)][0] or _title(i) != last[_k(i)][1]
    ]
    return items, fresh, not failed


async def _claim(
    session: AsyncSession, user_id: uuid.UUID | None, channel: str, slot: str, prio: int | None
) -> uuid.UUID | None:
    """Insert (or take over an ``aborted``) pending log row and COMMIT it; None when already handled."""
    stmt = (
        insert(NotificationSent)
        .values(id=uuid.uuid4(), user_id=user_id, channel=channel, slot=slot, priority=prio, status="pending")
        .on_conflict_do_nothing()
        .returning(NotificationSent.id)
    )
    row_id = await session.scalar(stmt)
    if row_id is None:
        row_id = await session.scalar(
            update(NotificationSent)
            .where(
                NotificationSent.user_id.is_not_distinct_from(user_id),
                NotificationSent.channel == channel,
                NotificationSent.slot == slot,
                NotificationSent.status == "aborted",
            )
            .values(status="pending", priority=prio, error_code=None)
            .returning(NotificationSent.id)
        )
    await session.commit()
    return row_id


async def _finish(
    session: AsyncSession, row_id: uuid.UUID, error: str | None, status: str = "failed"
) -> None:
    row = await session.get(NotificationSent, row_id)
    assert row is not None
    row.status, row.error_code = (status, error) if error else ("sent", None)
    row.sent_at = datetime.now(UTC)


async def _eligible(session: AsyncSession) -> list[User]:
    return list(
        (
            await session.scalars(
                select(User)
                .join(NotificationPref, NotificationPref.user_id == User.id)
                .where(
                    NotificationPref.email_digest.is_(True),
                    User.is_active.is_(True),
                    User.email.is_not(None),
                    User.email != "",
                )
                .order_by(User.id)
            )
        ).all()
    )


async def run(session: AsyncSession, now: datetime | None = None) -> dict[str, Any]:
    """Scheduled task body. Safe to call every few minutes: the slot log makes it idempotent."""
    now = now or datetime.now(UTC)
    cfg = await load(session)
    if not cfg.enabled or cfg.smtp_profile_id is None:
        return {"enabled": False}
    slots = due_slots(now, cfg.times)
    if not slots:
        return {"slots": []}
    try:
        _, ctx = await open_connection(session, "smtp", Purpose.WRITE, profile_id=cfg.smtp_profile_id)
    except AppError as exc:
        log.warning("notifications_smtp_unavailable", code=exc.code)
        return {"slots": slots, "error": exc.code}
    summary: dict[str, Any] = {"slots": slots, "sent": 0, "failed": 0, "skipped": 0}
    users = await _eligible(session)
    for slot in slots:
        if now - datetime.strptime(slot, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ) > LATE:
            log.warning("notification_slot_late", slot=slot)
        done = set(
            (
                await session.scalars(
                    select(NotificationSent.user_id).where(
                        NotificationSent.slot == slot,
                        NotificationSent.channel == "email",
                        NotificationSent.status != "aborted",
                    )
                )
            ).all()
        )
        mails = 0
        seen: dict[
            uuid.UUID, list[dict[str, Any]]
        ] = {}  # current items of people visited in this run (Teams)
        capped = False
        for u in users:
            if u.id in done:
                continue
            if mails >= cfg.max_per_run:
                capped = True  # the rest are visited next run; the claim keeps this idempotent
                break
            uid, to, name = u.id, str(u.email), u.display_name
            try:
                async with session.begin_nested():  # a failing read isolates this person only (C-27)
                    who = await _recipient(session, u)
                    items, fresh, complete = (
                        await _fresh_items(session, who) if who.has(DASHBOARD_VIEW) else ([], [], False)
                    )
            except Exception:
                log.exception("notification_recipient_failed")
                summary["failed"] += 1
                continue
            seen[uid] = items
            if not fresh:
                summary["skipped"] += 1
                if who.has(DASHBOARD_VIEW):
                    await _remember(session, uid, items, [], complete, now)  # forget what went away
                await session.commit()
                continue
            row_id = await _claim(session, uid, "email", slot, min(i["priority"] for i in fresh))
            if row_id is None:
                continue
            mails += 1
            subject, text, body = build_mail(name, fresh, cfg.app_base_url, now)
            status, error = "failed", None
            try:
                await smtp.send_mail(ctx, to=to, subject=subject, text=text, html=body)
            except smtp.SmtpError as exc:
                error = exc.code
            except AppError as exc:  # configuration refused before any connection (e.g. AUTH without TLS)
                error = exc.code
            except Exception:  # never leave the row pending after a known outcome
                log.exception("notification_send_failed")
                error = "smtp_error"
            if error in smtp.SYSTEM_CODES:
                status = "aborted"
            elif error == "smtp_unknown":  # timeout mid-send: delivery unknown, never resend (C-06)
                await session.commit()
                summary["aborted"] = error
                return summary
            await _finish(session, row_id, error, status)
            if status == "aborted":  # relay is broken: do not hammer it (or lock the account) for the others
                await session.commit()
                summary["aborted"] = error
                return summary
            if error:
                summary["failed"] += 1
            else:
                summary["sent"] += 1
                await _remember(session, uid, items, fresh, complete, now)
            await session.commit()
        if not capped and cfg.teams_profile_id is not None:
            await _post_team(session, cfg.teams_profile_id, slot, users, seen, now, summary)
    return summary


async def _remember(
    session: AsyncSession,
    user_id: uuid.UUID,
    items: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
    complete: bool,
    now: datetime,
) -> None:
    if fresh:
        stmt = insert(NotificationItem).values(
            [
                {
                    "user_id": user_id,
                    "item_key": _k(i),
                    "last_priority": i["priority"],
                    "last_title": _title(i),
                    "last_sent_at": now,
                }
                for i in fresh
            ]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["user_id", "item_key"],
                set_={
                    "last_priority": stmt.excluded.last_priority,
                    "last_title": stmt.excluded.last_title,
                    "last_sent_at": now,
                },
            )
        )
    if complete:  # an item that went away and comes back is news again; skip when a source failed
        keep = [_k(i) for i in items]
        await session.execute(
            delete(NotificationItem).where(
                NotificationItem.user_id == user_id, NotificationItem.item_key.not_in(keep)
            )
        )


async def _post_team(
    session: AsyncSession,
    profile_id: uuid.UUID,
    slot: str,
    users: list[User],
    seen: dict[uuid.UUID, list[dict[str, Any]]],
    now: datetime,
    summary: dict[str, Any],
) -> None:
    """Once per slot, after every recipient was visited: current backlog by priority, de-duplicated by key."""
    if await session.scalar(
        select(NotificationSent.id).where(NotificationSent.channel == "teams", NotificationSent.slot == slot)
    ):
        return
    by_key: dict[str, int] = {}
    for u in users:
        items = seen.get(u.id)
        if items is None:  # handled in an earlier run of this slot
            try:
                async with session.begin_nested():
                    who = await _recipient(session, u)
                    items = (await collect_next_actions(session, who))[0] if who.has(DASHBOARD_VIEW) else []
            except Exception:
                log.exception("notification_recipient_failed")
                summary["teams"] = "incomplete"
                return  # never post partial numbers
        for i in items:
            by_key[_k(i)] = min(i["priority"], by_key.get(_k(i), i["priority"]))
    counts: dict[int, int] = {}
    for p in by_key.values():
        counts[p] = counts.get(p, 0) + 1
    if not counts:
        return
    try:
        _, ctx = await open_connection(session, "teams", Purpose.WRITE, profile_id=profile_id)
    except AppError as exc:
        summary["teams"] = exc.code
        return
    row_id = await _claim(session, None, "teams", slot, min(counts))
    if row_id is None:
        return
    try:
        await teams.post_message(ctx, team_text(counts, len(users), now))
        error = None
    except teams.TeamsError as exc:
        error = exc.code
    except Exception:
        log.exception("teams_post_failed")
        error = "teams_error"
    await _finish(session, row_id, error)
    await session.commit()
    summary["teams"] = error or "sent"
