"""Scheduled "Việc tiếp theo" digest: fake SMTP transport + respx, never a real socket."""

from __future__ import annotations

import smtplib
from datetime import datetime
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import delete, select, update

from app.core.auth.models import User
from app.core.connectors import smtp, teams
from app.core.connectors.base import ConnectionContext, Purpose
from app.core.db import get_sessionmaker
from app.core.modules.manifest import NextActionSource
from app.core.notifications import service
from app.core.notifications.models import NotificationItem, NotificationPref, NotificationSent
from app.core.permissions import AUDIT_VIEW, DASHBOARD_VIEW
from app.core.timeutil import TZ
from tests.conftest import ApiClient
from tests.test_workload import _user

OP = "digest-op@test.local"
HOOK = "https://hooks.example.test/webhook/SECRETSECRET"


class Crash(BaseException):
    """Simulates the process dying between the commit and the send."""


class FakeSMTP:
    sent: ClassVar[list[Any]] = []
    calls: ClassVar[list[str]] = []

    def __init__(self, host: str, port: int, timeout: float = 0) -> None:
        FakeSMTP.calls.append("connect")

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, context: Any = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")

    def noop(self) -> None:
        self.calls.append("noop")

    def send_message(self, msg: Any) -> None:
        self.calls.append("send_message")
        FakeSMTP.sent.append(msg)

    def quit(self) -> None:
        self.calls.append("quit")

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def mail(monkeypatch):  # type: ignore[no-untyped-def]
    FakeSMTP.sent, FakeSMTP.calls = [], []
    monkeypatch.setattr(smtp, "smtp_factory", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def queue(monkeypatch):  # type: ignore[no-untyped-def]
    """Two next-action sources: one anyone with the dashboard may see, one needing audit.view."""
    items: dict[str, list[dict[str, Any]]] = {"open": [], "audit": []}

    async def load_open(session: Any, user: Any) -> list[dict[str, Any]]:
        return list(items["open"])

    async def load_audit(session: Any, user: Any) -> list[dict[str, Any]]:
        return list(items["audit"])

    sources = [
        NextActionSource("t.open", DASHBOARD_VIEW, load_open),
        NextActionSource("t.audit", AUDIT_VIEW, load_audit),
    ]
    fake = SimpleNamespace(modules=[SimpleNamespace(name="Test", next_actions=sources)])
    monkeypatch.setattr("app.core.dashboard.service.get_registry", lambda: fake)
    return items


def item(key: str, priority: int, title: str = "Việc <b>thử</b>") -> dict[str, Any]:
    return {
        "key": key,
        "kind": "t",
        "priority": priority,
        "title": title,
        "detail": "d",
        "due": "2026-12-01",
        "href": "/me/work",
        "action": "x",
    }


async def setup_env(admin: ApiClient, *, teams_on: bool = False, enabled: bool = True) -> None:
    await _user(admin, "operator", OP)
    profiles = {p["name"]: p for p in (await admin.get("/api/core/connections")).json()}
    ids: dict[str, str] = {}
    for name, kind, cfg, base, cred in (
        (
            "SMTP T",
            "smtp",
            {"host": "smtp.test", "port": 25, "tls_mode": "starttls", "from_address": "ops@test"},
            "https://smtp.test",
            {"auth_type": "smtp_login", "public": {"username": "u"}, "secrets": {"password": "p"}},
        ),
        (
            "Teams T",
            "teams",
            {},
            "https://teams.test",
            {"auth_type": "webhook_url", "secrets": {"webhook_url": HOOK}},
        ),
    ):
        prof = (
            profiles.get(name)
            or (
                await admin.post(
                    "/api/core/connections",
                    json={"system_type": kind, "name": name, "base_url": base, "config": cfg},
                )
            ).json()
        )
        r = await admin.put(f"/api/core/connections/{prof['id']}/credentials/write", json=cred)
        assert r.status_code == 200, r.text
        ids[kind] = prof["id"]
    r = await admin.put(
        "/api/core/notifications/settings",
        json={
            "enabled": enabled,
            "smtp_profile_id": ids["smtp"],
            "teams_profile_id": ids["teams"] if teams_on else None,
            "app_base_url": "https://ops.test",
        },
    )
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as s:
        await s.execute(delete(NotificationSent))
        await s.execute(delete(NotificationItem))
        await s.execute(delete(NotificationPref))  # other tests leave opted-in people behind
        await s.execute(
            update(User)
            .where(User.upn == OP)
            .values(email="op@test.local", is_active=True, display_name="Nguyễn <Op>")
        )
        await s.commit()


async def operator(app: Any, on: bool = True) -> ApiClient:
    c = ApiClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    await c.login(OP)
    r = await c.put("/api/core/me/notifications", json={"email_digest": on})
    assert r.status_code == 200, r.text
    return c


def at(h: int, m: int, day: int = 28) -> datetime:
    return datetime(2026, 11, day, h, m, tzinfo=TZ)


async def run_at(now: datetime) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        out = await service.run(s, now)
        await s.commit()
        return out


async def test_off_by_default_sends_nothing(admin, app, mail, queue) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin, enabled=False)
    await operator(app)
    queue["open"] = [item("a", 1)]
    assert (await run_at(at(8, 1)))["enabled"] is False
    assert mail.sent == []
    assert service.NotificationSettings().enabled is False


async def test_digest_only_has_items_the_person_may_see_and_escapes_html(admin, app, mail, queue) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    queue["open"] = [item("a", 1)]
    queue["audit"] = [item("secret", 0, "Chỉ quản lý thấy")]
    out = await run_at(at(8, 1))
    assert out["sent"] == 1 and len(mail.sent) == 1
    msg = mail.sent[0]
    plain, body = msg.get_body(("plain",)).get_content(), msg.get_body(("html",)).get_content()
    assert "Chỉ quản lý thấy" not in body + plain
    assert "&lt;b&gt;thử&lt;/b&gt;" in body and "<b>thử" not in body and "Nguyễn &lt;Op&gt;" in body
    assert "01/12/2026" in body and "https://ops.test/me/work" in body
    assert msg["Subject"] == "infra-ops – Việc tiếp theo 28/11/2026 08:01" and msg["To"] == "op@test.local"


async def test_repeat_rules(admin, app, mail, queue) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    queue["open"] = [item("a", 2)]
    await run_at(at(8, 1))
    assert len(mail.sent) == 1
    await run_at(at(8, 6))  # same slot: logged already
    await run_at(at(13, 31))  # next slot, nothing changed
    assert len(mail.sent) == 1
    queue["open"] = [item("a", 1)]  # more urgent -> again
    await run_at(at(8, 2, day=29))
    assert len(mail.sent) == 2
    queue["open"] = [item("a", 3)]  # less urgent -> quiet
    await run_at(at(13, 32, day=29))
    assert len(mail.sent) == 2
    queue["open"] = []  # solved, later back -> news again
    await run_at(at(8, 3, day=30))
    queue["open"] = [item("a", 1)]
    await run_at(at(13, 33, day=30))
    assert len(mail.sent) == 3


async def test_skips_inactive_no_email_and_opted_out(admin, app, mail, queue) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    queue["open"] = [item("a", 1)]
    async with get_sessionmaker()() as s:
        await s.execute(update(User).where(User.upn == OP).values(email=None))
        await s.commit()
    assert (await run_at(at(8, 1)))["sent"] == 0
    async with get_sessionmaker()() as s:
        await s.execute(update(User).where(User.upn == OP).values(email="op@test.local", is_active=False))
        await s.commit()
    assert (await run_at(at(8, 2)))["sent"] == 0
    async with get_sessionmaker()() as s:
        await s.execute(update(User).where(User.upn == OP).values(is_active=True))
        await s.commit()
    await operator(app, on=False)
    assert (await run_at(at(8, 3)))["sent"] == 0 and mail.sent == []


async def test_crash_after_commit_is_never_resent(admin, app, mail, queue, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    queue["open"] = [item("a", 1)]

    async def boom(*a: Any, **k: Any) -> None:
        raise Crash

    monkeypatch.setattr(smtp, "send_mail", boom)
    with pytest.raises(Crash):
        await run_at(at(8, 1))
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(NotificationSent.status))).all() == ["pending"]
    monkeypatch.undo()
    monkeypatch.setattr(smtp, "smtp_factory", FakeSMTP)
    assert (await run_at(at(8, 6)))["sent"] == 0 and mail.sent == []


async def test_send_failure_is_logged_with_a_code_only(admin, app, mail, queue, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    queue["open"] = [item("a", 1)]

    def refuse(self: Any, msg: Any) -> None:
        raise smtplib.SMTPRecipientsRefused({"op@test.local": (550, b"no such user op@test.local")})

    monkeypatch.setattr(FakeSMTP, "send_message", refuse)
    assert (await run_at(at(8, 1)))["failed"] == 1
    async with get_sessionmaker()() as s:
        row = (await s.scalars(select(NotificationSent))).one()
        assert (row.status, row.error_code) == ("failed", "smtp_refused")
        assert not (await s.scalars(select(NotificationItem))).all()  # not remembered, but also not retried
    assert (await run_at(at(8, 6)))["failed"] == 0


async def test_teams_gets_totals_only(admin, app, mail, queue, respx_mock) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin, teams_on=True)
    await operator(app)
    queue["open"] = [item("a", 1, "Tên riêng tư"), item("b", 1), item("c", 3)]
    route = respx_mock.post(HOOK).mock(return_value=httpx.Response(200, text="1"))
    assert (await run_at(at(8, 1)))["teams"] == "sent"
    await run_at(at(8, 6))
    assert route.call_count == 1
    text = route.calls[0].request.content.decode()
    assert "Quá hạn / khẩn: 2" in text and "Cần biết: 1" in text
    for private in ("Tên riêng tư", "Nguyễn", OP, "op@test.local"):
        assert private not in text


async def test_test_connection_sends_nothing(mail, respx_mock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ctx = ConnectionContext(
        "https://smtp.test",
        smtp.SmtpConfig(host="smtp.test", port=25, tls_mode="starttls", from_address="a@b"),
        Purpose.WRITE,
        "smtp_login",
        {"username": "u"},
        {"password": "p"},
    )
    report = await smtp.SmtpConnector().test_connection(ctx)
    assert report.ok and not {"send_message", "mail", "rcpt"} & set(mail.calls)
    assert mail.calls[:5] == ["connect", "ehlo", "starttls", "ehlo", "login"] and mail.calls[-1] == "quit"

    async def reach(report: Any, url: str, **kw: Any) -> bool:
        assert url == HOOK
        return True

    monkeypatch.setattr(teams, "check_reachability", reach)
    tctx = ConnectionContext(
        "https://teams.test", teams.TeamsConfig(), Purpose.WRITE, "webhook_url", {}, {"webhook_url": HOOK}
    )
    assert (await teams.TeamsConnector().test_connection(tctx)).ok
    assert respx_mock.calls.call_count == 0


async def test_preference_api_audits_and_settings_need_permission(admin, app, queue) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    op = await operator(app, on=False)
    assert (await op.get("/api/core/me/notifications")).json()["email_digest"] is False
    assert (await op.get("/api/core/notifications/settings")).status_code == 403
    assert (await op.get("/api/core/notifications/profiles")).status_code == 403
    assert {p["name"] for p in (await admin.get("/api/core/notifications/profiles")).json()["smtp"]} >= {
        "SMTP T"
    }
    assert (await op.put("/api/core/notifications/settings", json={"enabled": True})).status_code == 403
    await op.put("/api/core/me/notifications", json={"email_digest": True})
    audit = (await admin.get("/api/core/audit", params={"q": "notifications.pref_changed"})).text
    assert "notifications.pref_changed" in audit
    bad = await admin.put("/api/core/notifications/settings", json={"times": ["25:00"]})
    assert bad.status_code == 400


async def test_me_next_is_unchanged(admin, app) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    op = await operator(app)
    data = (await op.get("/api/core/me/next")).json()
    assert set(data) == {"items", "failed"} and isinstance(data["items"], list)
    assert data["items"] == sorted(
        data["items"], key=lambda i: (i["priority"], i.get("due") is None, str(i.get("due") or ""))
    )


def test_due_slots() -> None:
    times = ["08:00", "13:30", "23:55"]
    assert service.due_slots(at(8, 14), times) == ["2026-11-28T08:00"]
    assert service.due_slots(at(8, 15), times) == ["2026-11-28T08:00"]  # window is 90 minutes (C-24)
    assert service.due_slots(at(9, 30), times) == []
    assert service.due_slots(at(7, 59), times) == []
    assert service.due_slots(at(0, 4, day=29), times) == ["2026-11-28T23:55"]


OP2, OP3 = "digest-op2@test.local", "digest-op3@test.local"


async def extra_operators(admin: ApiClient, app: Any, *upns: str) -> None:
    for n, upn in enumerate(upns):
        await _user(admin, "operator", upn)
        async with get_sessionmaker()() as s:
            await s.execute(
                update(User).where(User.upn == upn).values(email=f"e{n}@test.local", is_active=True)
            )
            await s.commit()
        c = ApiClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        await c.login(upn)
        assert (await c.put("/api/core/me/notifications", json={"email_digest": True})).status_code == 200


async def cap_mails(admin: ApiClient) -> Any:
    cfg = (await admin.get("/api/core/notifications/settings")).json()
    return await admin.put("/api/core/notifications/settings", json={**cfg, "max_per_run": 1})


def recipients() -> set[str]:
    return {m["To"] for m in FakeSMTP.sent}


async def test_slot_is_still_served_late(admin, app, mail, queue) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    queue["open"] = [item("a", 1)]
    assert (await run_at(at(9, 20)))["sent"] == 1  # the 08:00 slot, worker was busy (C-24)
    assert (await run_at(at(9, 31))).get("sent", 0) == 0


async def test_auth_failure_stops_after_one_login_and_is_retried(
    admin, app, mail, queue, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    await extra_operators(admin, app, OP2, OP3)
    queue["open"] = [item("a", 1)]

    def deny(self: Any, user: str, password: str) -> None:
        self.calls.append("login")
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials op@test.local")

    monkeypatch.setattr(FakeSMTP, "login", deny)
    out = await run_at(at(8, 1))
    assert out["aborted"] == "smtp_auth" and mail.calls.count("login") == 1 and mail.sent == []
    async with get_sessionmaker()() as s:
        row = (await s.scalars(select(NotificationSent))).one()
        assert (row.status, row.error_code) == ("aborted", "smtp_auth")
    monkeypatch.undo()
    monkeypatch.setattr(smtp, "smtp_factory", FakeSMTP)
    assert (await run_at(at(8, 6)))["sent"] == 3  # the aborted row is claimed again, nobody was lost
    assert len(recipients()) == 3


async def test_timeout_mid_send_stays_pending(admin, app, mail, queue, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    queue["open"] = [item("a", 1)]

    def hang(self: Any, msg: Any) -> None:
        raise TimeoutError

    monkeypatch.setattr(FakeSMTP, "send_message", hang)
    assert (await run_at(at(8, 1)))["aborted"] == "smtp_unknown"
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(NotificationSent.status))).all() == ["pending"]
    assert (await run_at(at(8, 6))).get("sent") == 0


async def test_max_per_run_does_not_starve_later_people(admin, app, mail, queue) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin, teams_on=True)
    await operator(app)
    await extra_operators(admin, app, OP2, OP3)
    r = await cap_mails(admin)
    assert r.status_code == 200, r.text
    queue["open"] = [item("a", 1)]
    for minute in (1, 6, 11):
        assert (await run_at(at(8, minute)))["sent"] == 1
    assert (await run_at(at(8, 16)))["sent"] == 0  # all three are done
    assert len(mail.sent) == 3 and len(recipients()) == 3


async def test_title_change_resends(admin, app, mail, queue) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    queue["open"] = [item("job:1", 2, "Bước 1")]
    await run_at(at(8, 1))
    await run_at(at(13, 31))
    assert len(mail.sent) == 1
    queue["open"] = [item("job:1", 2, "Bước 2")]  # same key and priority, new step
    await run_at(at(8, 2, day=29))
    assert len(mail.sent) == 2


async def test_long_keys_are_compared_consistently(admin, app, mail, queue) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    await operator(app)
    queue["open"] = [item("k" * 300, 2)]
    await run_at(at(8, 1))
    await run_at(at(13, 31))
    assert len(mail.sent) == 1


async def test_failing_read_isolates_one_recipient(admin, app, mail, queue, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    await setup_env(admin)
    await operator(app)
    await extra_operators(admin, app, OP2)
    queue["open"] = [item("a", 1)]
    real = service.collect_next_actions

    async def flaky(session: Any, who: Any) -> Any:
        if who.upn == OP:
            await session.execute(text("select * from no_such_table"))  # a DB error poisons the transaction
        return await real(session, who)

    monkeypatch.setattr(service, "collect_next_actions", flaky)
    out = await run_at(at(8, 1))
    assert out["sent"] == 1 and out["failed"] == 1 and recipients() == {"e0@test.local"}


async def test_teams_counts_current_backlog_deduped_after_all_recipients(  # type: ignore[no-untyped-def]
    admin, app, mail, queue, respx_mock
) -> None:
    await setup_env(admin, teams_on=True)
    await operator(app)
    await extra_operators(admin, app, OP2)
    r = await cap_mails(admin)
    assert r.status_code == 200, r.text
    queue["open"] = [item("shared", 1), item("b", 3)]  # both people see the same two items
    route = respx_mock.post(HOOK).mock(return_value=httpx.Response(200, text="1"))
    await run_at(at(8, 1))
    assert route.call_count == 0  # one recipient is still waiting
    await run_at(at(8, 6))
    assert route.call_count == 1
    text = route.calls[0].request.content.decode()
    assert "Quá hạn / khẩn: 1" in text and "Cần biết: 1" in text  # deduped by key, not 2 per person
    await run_at(at(8, 11))
    assert route.call_count == 1


async def test_run_once_wiring_with_real_registry(admin, app, mail, queue, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.modules.registry import get_registry
    from app.core.scheduler.models import ScheduledTaskRow
    from app.core.scheduler.service import run_once

    await setup_env(admin)
    await operator(app)
    await extra_operators(admin, app, OP2)
    queue["open"] = [item("a", 1)]
    monkeypatch.setattr(service, "due_slots", lambda now, times: ["2026-11-28T08:00"])
    async with get_sessionmaker()() as s:
        await s.execute(
            update(ScheduledTaskRow)
            .where(ScheduledTaskRow.key == "core.notifications")
            .values(next_run_at=datetime(2020, 1, 1, tzinfo=TZ), lease_until=None, enabled=True)
        )
        await s.commit()
    ran = [await run_once(get_sessionmaker(), get_registry()) for _ in range(5)]
    assert "core.notifications" in ran
    assert len(mail.sent) == 2


async def test_webhook_has_no_hint_and_invalid_url_is_a_code(admin, respx_mock) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin, teams_on=True)
    prof = next(p for p in (await admin.get("/api/core/connections")).json() if p["name"] == "Teams T")
    detail = (await admin.get(f"/api/core/connections/{prof['id']}")).text
    assert "SECRETSECRET"[-4:] not in detail and HOOK[-4:] not in detail
    ctx = ConnectionContext(
        "https://teams.test",
        teams.TeamsConfig(),
        Purpose.WRITE,
        "webhook_url",
        {},
        {"webhook_url": "https://host:abc/"},
    )
    with pytest.raises(teams.TeamsError) as exc:
        await teams.post_message(ctx, "x")
    assert exc.value.code == "teams_invalid_webhook"


async def test_changing_host_port_or_tls_clears_credentials(admin) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    prof = next(p for p in (await admin.get("/api/core/connections")).json() if p["name"] == "SMTP T")
    cfg = {"host": "smtp.test", "port": 25, "tls_mode": "starttls", "from_address": "ops@test"}
    same = await admin.patch(
        f"/api/core/connections/{prof['id']}",
        json={"version": prof["version"], "config": {**cfg, "from_address": "other@test"}},
    )
    assert same.status_code == 200 and not same.json()["credentials_cleared"]
    moved = await admin.patch(
        f"/api/core/connections/{prof['id']}",
        json={"version": same.json()["version"], "config": {**cfg, "host": "evil.test"}},
    )
    assert moved.status_code == 200 and moved.json()["credentials_cleared"] is True
    assert moved.json()["credentials"] == {"read": None, "write": None}


async def test_no_login_without_encryption_and_ssl_mode(mail, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.errors import AppError

    def ctx(mode: str, user: str = "u") -> ConnectionContext:
        return ConnectionContext(
            "https://smtp.test",
            smtp.SmtpConfig(host="smtp.test", port=465, tls_mode=mode, from_address="a@b"),  # type: ignore[arg-type]
            Purpose.WRITE,
            "smtp_login",
            {"username": user},
            {"password": "p"},
        )

    with pytest.raises(AppError) as exc:
        await smtp.send_mail(ctx("none"), to="x@y.z", subject="s", text="t", html="<p>t</p>")
    assert exc.value.code == "smtp_insecure_auth" and mail.calls == []
    report = await smtp.SmtpConnector().test_connection(ctx("none"))
    assert not report.ok and mail.calls == []
    monkeypatch.setattr(smtp, "smtp_factory", None)  # the plain factory must not be used for ssl
    monkeypatch.setattr(smtp, "smtp_ssl_factory", FakeSMTP)
    await smtp.send_mail(ctx("ssl"), to="x@y.z", subject="s", text="t", html="<p>t</p>")
    assert "starttls" not in mail.calls and "send_message" in mail.calls
    mail.calls.clear()
    monkeypatch.setattr(smtp, "smtp_factory", FakeSMTP)
    await smtp.send_mail(ctx("none", user=""), to="x@y.z", subject="s", text="t", html="<p>t</p>")
    assert "login" not in mail.calls


async def test_my_notifications_reports_missing_email(admin, app) -> None:  # type: ignore[no-untyped-def]
    await setup_env(admin)
    op = await operator(app)
    assert (await op.get("/api/core/me/notifications")).json()["has_email"] is True
    async with get_sessionmaker()() as s:
        await s.execute(update(User).where(User.upn == OP).values(email=None))
        await s.commit()
    assert (await op.get("/api/core/me/notifications")).json()["has_email"] is False
